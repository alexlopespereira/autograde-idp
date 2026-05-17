"""Tests para cmd_login + _complete_profile_if_needed (US-02 — Fatia 5).

Cobre os 5 caminhos:
- AC2: identity já tem github_username → não prompta, não posta.
- AC3: is_interactive() False → não prompta, warn no stderr.
- AC4: happy prompt + POST 200 → print 'Perfil completo: ...'.
- AC5: POST 400 → erro stderr, mas return 0.
- AC6: fetch_me_identity raise ConnectionError → warn + return 0.
- AC7: decode_id_token_unverified success / AuthError → nome resolvido / vazio.
- AC8: __version__ == '0.3.0' + pyproject bate.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
import requests

from autograde_idp import __version__, cli
from autograde_idp.auth import AuthError, TokenBundle
from autograde_idp.notas import HttpError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle() -> TokenBundle:
    return TokenBundle(
        access_token="acc",
        refresh_token="ref",
        id_token="header.payload.sig",
        expires_at=9_999_999_999.0,
        first_login_at=1_700_000_000.0,
        client_id="cid",
    )


@pytest.fixture
def patched_login(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Monkeypatch device_login + save_token + token_path + load_client_id.

    Devolve dict com bundles capturados — testes individuais sobrepõem
    fetch_me_identity / post_me_profile / is_interactive / prompt_github_username
    conforme o caminho que querem exercer.
    """
    bundle = _make_bundle()
    saved: list[TokenBundle] = []

    def fake_device_login(client_id, api, on_user_code=None):  # noqa: ARG001
        return bundle

    def fake_save_token(b):
        saved.append(b)
        return Path("/tmp/fake-token.json")

    monkeypatch.setattr(cli, "load_client_id", lambda: "cid")
    monkeypatch.setattr(cli, "device_login", fake_device_login)
    monkeypatch.setattr(cli, "save_token", fake_save_token)
    monkeypatch.setattr(cli, "token_path", lambda: Path("/tmp/fake-token.json"))
    # default: assume interactive shell + name decodable
    monkeypatch.setattr(cli, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "decode_id_token_unverified", lambda _t: {"name": "Aluno X"})
    return {"bundle": bundle, "saved": saved}


# ---------------------------------------------------------------------------
# AC2 — identity already complete
# ---------------------------------------------------------------------------


def test_login_skips_prompt_when_github_username_set(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompted: list[int] = []
    posted: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        cli,
        "fetch_me_identity",
        lambda api, tok: {"email": "x@y.com", "github_username": "existing", "nome": "X"},
    )
    monkeypatch.setattr(
        cli,
        "prompt_github_username",
        lambda **_k: prompted.append(1) or "should-not-call",
    )
    monkeypatch.setattr(
        cli,
        "post_me_profile",
        lambda *a, **k: posted.append(a) or {},
    )

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert prompted == [], "prompt_github_username NÃO devia ser chamado"
    assert posted == [], "post_me_profile NÃO devia ser chamado"
    assert "Login OK" in captured.out
    assert "Perfil completo" not in captured.out


# ---------------------------------------------------------------------------
# AC3 — non-interactive shell
# ---------------------------------------------------------------------------


def test_login_non_interactive_skips_profile_with_warn(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "is_interactive", lambda: False)

    prompted: list[int] = []
    fetched: list[int] = []
    monkeypatch.setattr(
        cli, "fetch_me_identity", lambda *a, **k: fetched.append(1) or {}
    )
    monkeypatch.setattr(
        cli, "prompt_github_username", lambda **_k: prompted.append(1) or "x"
    )

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert prompted == []
    assert fetched == [], "fetch_me_identity NÃO devia ser chamado em non-tty"
    assert "perfil incompleto" in captured.err
    assert "interativo" in captured.err


# ---------------------------------------------------------------------------
# AC4 — happy prompt path
# ---------------------------------------------------------------------------


def test_login_happy_prompts_and_posts(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "fetch_me_identity",
        lambda api, tok: {"email": "a@b.com", "github_username": "", "nome": "Aluno X"},
    )
    captured_post: dict[str, Any] = {}

    def fake_post(api, tok, nome, gh):
        captured_post["api"] = api
        captured_post["nome"] = nome
        captured_post["gh"] = gh
        return {"updated": ["nome", "github_username"], "skipped": []}

    monkeypatch.setattr(cli, "post_me_profile", fake_post)
    monkeypatch.setattr(cli, "prompt_github_username", lambda **_k: "foo-bar")

    rc = cli.cmd_login(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert captured_post == {
        "api": cli.api_url(),
        "nome": "Aluno X",
        "gh": "foo-bar",
    }
    assert "Perfil completo: nome=Aluno X github=foo-bar" in out


# ---------------------------------------------------------------------------
# AC5 — POST 400 invalid_github_username
# ---------------------------------------------------------------------------


def test_login_post_400_logs_error_but_returns_zero(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "fetch_me_identity",
        lambda api, tok: {"github_username": ""},
    )

    def boom_post(*_a, **_k):
        raise HttpError(400, '{"error":"invalid_github_username"}')

    monkeypatch.setattr(cli, "post_me_profile", boom_post)
    monkeypatch.setattr(cli, "prompt_github_username", lambda **_k: "foo--bar")

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert "erro ao salvar perfil: HTTP 400" in captured.err
    assert "invalid_github_username" in captured.err
    assert "Perfil completo" not in captured.out


# ---------------------------------------------------------------------------
# AC6 — fetch_me_identity rede/5xx
# ---------------------------------------------------------------------------


def test_login_fetch_identity_connection_error_skips_profile(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom_get(*_a, **_k):
        raise requests.ConnectionError("network down")

    posted: list[int] = []
    prompted: list[int] = []
    monkeypatch.setattr(cli, "fetch_me_identity", boom_get)
    monkeypatch.setattr(cli, "post_me_profile", lambda *a, **k: posted.append(1) or {})
    monkeypatch.setattr(
        cli, "prompt_github_username", lambda **_k: prompted.append(1) or "x"
    )

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert posted == []
    assert prompted == []
    assert "não foi possível verificar perfil" in captured.err
    assert "network down" in captured.err


def test_login_fetch_identity_5xx_skips_profile(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom_get(*_a, **_k):
        raise HttpError(503, "service unavailable")

    posted: list[int] = []
    monkeypatch.setattr(cli, "fetch_me_identity", boom_get)
    monkeypatch.setattr(cli, "post_me_profile", lambda *a, **k: posted.append(1) or {})
    monkeypatch.setattr(cli, "prompt_github_username", lambda **_k: "x")

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert posted == []
    assert "status 503" in captured.err


# ---------------------------------------------------------------------------
# AC7 — name resolution via decode_id_token_unverified
# ---------------------------------------------------------------------------


def test_login_name_falls_back_to_empty_when_decode_fails(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom_decode(_t):
        raise AuthError("id_token mal formado")

    captured_post: dict[str, Any] = {}

    def fake_post(api, tok, nome, gh):
        captured_post["nome"] = nome
        captured_post["gh"] = gh
        return {"updated": ["github_username"], "skipped": ["nome"]}

    monkeypatch.setattr(cli, "decode_id_token_unverified", boom_decode)
    monkeypatch.setattr(
        cli, "fetch_me_identity", lambda api, tok: {"github_username": ""}
    )
    monkeypatch.setattr(cli, "post_me_profile", fake_post)
    monkeypatch.setattr(cli, "prompt_github_username", lambda **_k: "foo")

    rc = cli.cmd_login(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert captured_post == {"nome": "", "gh": "foo"}
    assert "Perfil completo: nome= github=foo" in out


def test_login_name_falls_back_to_empty_when_payload_missing_name(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured_post: dict[str, Any] = {}

    def fake_post(api, tok, nome, gh):
        captured_post["nome"] = nome
        captured_post["gh"] = gh
        return {"updated": ["github_username"], "skipped": ["nome"]}

    monkeypatch.setattr(cli, "decode_id_token_unverified", lambda _t: {"email": "x@y.com"})
    monkeypatch.setattr(
        cli, "fetch_me_identity", lambda api, tok: {"github_username": ""}
    )
    monkeypatch.setattr(cli, "post_me_profile", fake_post)
    monkeypatch.setattr(cli, "prompt_github_username", lambda **_k: "bar")

    rc = cli.cmd_login(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert captured_post == {"nome": "", "gh": "bar"}
    assert "github=bar" in out


# ---------------------------------------------------------------------------
# AC8 — versão
# ---------------------------------------------------------------------------


def test_version_is_0_3_0() -> None:
    assert __version__ == "0.3.0"


def test_pyproject_version_matches() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.3.0"' in text


# ---------------------------------------------------------------------------
# AC1 — wrapper catch-all em cmd_login
# ---------------------------------------------------------------------------


def test_cmd_login_swallows_unexpected_exception_from_profile_step(
    patched_login: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "_complete_profile_if_needed", boom)

    rc = cli.cmd_login(argparse.Namespace())
    captured = capsys.readouterr()

    assert rc == 0
    assert "perfil não atualizado" in captured.err
    assert "unexpected" in captured.err
