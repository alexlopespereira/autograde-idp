"""Testes do comando autograde notas (US-10).

Cobre: render tabular, lista vazia, token expirado (refresh auto), backend 503,
e helper me_identity_call usado pelo whoami.
"""
from __future__ import annotations

import json

import pytest
import requests

from autograde_idp import notas
from autograde_idp.auth import TokenBundle, TokenExpiredError


@pytest.fixture
def fake_bundle() -> TokenBundle:
    return TokenBundle(
        access_token="at-test",
        refresh_token="rt-test",
        id_token="h.e.s",
        expires_at=1e12,
        first_login_at=1e9,
        client_id="cid",
    )


@pytest.fixture
def with_fresh_token(monkeypatch: pytest.MonkeyPatch, fake_bundle: TokenBundle):
    monkeypatch.setattr(notas, "load_token", lambda: fake_bundle)
    monkeypatch.setattr(notas, "load_oauth_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(notas, "ensure_fresh_token", lambda b, *_a, **_k: b)
    return fake_bundle


class FakeResp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


def test_render_grades_table_contains_header_and_rows() -> None:
    grades = [
        {
            "exercicio": "1.1",
            "melhor_nota": 80,
            "num_tentativas": 2,
            "ultima_submissao_at": "2026-05-09T10:00:00+00:00",
        },
        {
            "exercicio": "1.2",
            "melhor_nota": 100,
            "num_tentativas": 1,
            "ultima_submissao_at": "2026-05-09T11:00:00+00:00",
        },
    ]
    table = notas.render_grades_table(grades)
    assert "Exercício" in table
    assert "Melhor Nota" in table
    assert "Tentativas" in table
    assert "Última Submissão" in table
    assert "1.1" in table
    assert "80" in table
    assert "1.2" in table
    assert "100" in table


def test_format_iso_local_handles_invalid_string() -> None:
    assert notas._format_iso_local("não-é-iso") == "não-é-iso"
    assert notas._format_iso_local("") == ""


def test_run_notas_happy_path_prints_table(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResp(
            200,
            {
                "grades": [
                    {
                        "exercicio": "1.1",
                        "melhor_nota": 80,
                        "num_tentativas": 2,
                        "ultima_submissao_at": "2026-05-09T10:00:00+00:00",
                    }
                ]
            },
        )

    monkeypatch.setattr(notas.requests, "get", fake_get)

    rc = notas.run_notas()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Exercício" in out
    assert "1.1" in out
    assert "80" in out
    assert captured["url"] == "http://test.local/me/grades"
    assert captured["headers"] == {"Authorization": "Bearer at-test"}


def test_run_notas_empty_list_prints_message(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        notas.requests, "get", lambda url, headers=None, timeout=None: FakeResp(200, {"grades": []})
    )

    rc = notas.run_notas()
    assert rc == 0
    out = capsys.readouterr().out
    assert notas.EMPTY_MESSAGE in out


def test_run_notas_token_expired_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    fake_bundle: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(notas, "load_token", lambda: fake_bundle)
    monkeypatch.setattr(notas, "load_oauth_credentials", lambda: ("cid", "secret"))

    def fake_refresh(_b, *_a, **_k):
        raise TokenExpiredError("refresh token inválido/expirado: invalid_grant")

    monkeypatch.setattr(notas, "ensure_fresh_token", fake_refresh)

    def must_not_get(*_a, **_k):
        raise AssertionError("HTTP não deveria ser chamado quando token expira")

    monkeypatch.setattr(notas.requests, "get", must_not_get)

    rc = notas.run_notas()
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "sessão expirada" in err or "expirad" in err


def test_run_notas_refreshes_token_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    fake_bundle: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bundle vencido é renovado por ensure_fresh_token antes do GET."""
    refreshed = TokenBundle(
        access_token="at-NEW",
        refresh_token=fake_bundle.refresh_token,
        id_token=fake_bundle.id_token,
        expires_at=2e12,
        first_login_at=fake_bundle.first_login_at,
        client_id=fake_bundle.client_id,
    )
    monkeypatch.setattr(notas, "load_token", lambda: fake_bundle)
    monkeypatch.setattr(notas, "load_oauth_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(notas, "ensure_fresh_token", lambda b, *_a, **_k: refreshed)

    seen: dict = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return FakeResp(200, {"grades": []})

    monkeypatch.setattr(notas.requests, "get", fake_get)

    rc = notas.run_notas()
    assert rc == 0
    assert seen["headers"] == {"Authorization": "Bearer at-NEW"}


def test_run_notas_backend_503_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        notas.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(503, {"error": "sheets_drop_detected"}),
    )

    rc = notas.run_notas()
    assert rc == 3
    err = capsys.readouterr().err
    assert "503" in err


def test_run_notas_backend_4xx_prints_message_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        notas.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(404, {"error": "not_found"}),
    )
    rc = notas.run_notas()
    assert rc == 1
    err = capsys.readouterr().err
    assert "404" in err
    assert "not_found" in err


def test_run_notas_backend_401_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        notas.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(401, {"error": "invalid_token"}),
    )
    rc = notas.run_notas()
    assert rc == 2


def test_run_notas_network_error_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_a, **_k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(notas.requests, "get", boom)
    rc = notas.run_notas()
    assert rc == 3
    assert "rede" in capsys.readouterr().err.lower()


def test_run_notas_no_session_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(notas, "load_token", lambda: None)

    rc = notas.run_notas()
    assert rc == 2
    assert "login" in capsys.readouterr().err.lower()


def test_me_identity_call_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        notas.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(
            200, {"email": "x@y.com", "nome": "X", "turma": "TD-2026-01"}
        ),
    )
    payload = notas.me_identity_call("http://test.local", "tok")
    assert payload == {"email": "x@y.com", "nome": "X", "turma": "TD-2026-01"}


# ---------------------------------------------------------------------------
# cmd_whoami integration (cobre AC5: turma renderizada via /me/identity)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_with_fresh_token(monkeypatch: pytest.MonkeyPatch, fake_bundle: TokenBundle):
    """Patcha as deps de auth importadas em cli.py."""
    from autograde_idp import cli

    monkeypatch.setattr(cli, "load_token", lambda: fake_bundle)
    monkeypatch.setattr(cli, "load_oauth_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(cli, "ensure_fresh_token", lambda b, *_a, **_k: b)
    monkeypatch.setattr(cli, "token_age_days", lambda _b: 7)
    return fake_bundle


def test_cmd_whoami_happy_path_prints_turma(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autograde_idp import cli

    monkeypatch.setattr(
        cli,
        "me_identity_call",
        lambda _api, _tok: {"email": "aluno@idp.edu.br", "nome": "Aluno X", "turma": "TD-2026-01"},
    )
    rc = cli.main(["whoami"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "aluno@idp.edu.br" in out
    assert "Aluno X" in out
    assert "TD-2026-01" in out
    assert "token_age_days" in out


def test_cmd_whoami_backend_4xx_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autograde_idp import cli

    def boom(_api, _tok):
        raise notas.HttpError(403, '{"error":"not_in_roster"}')

    monkeypatch.setattr(cli, "me_identity_call", boom)
    rc = cli.main(["whoami"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "403" in err
    assert "not_in_roster" in err


def test_cmd_whoami_backend_5xx_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autograde_idp import cli

    def boom(_api, _tok):
        raise notas.HttpError(503, '{"error":"unavailable"}')

    monkeypatch.setattr(cli, "me_identity_call", boom)
    rc = cli.main(["whoami"])
    err = capsys.readouterr().err
    assert rc == 3
    assert "503" in err


def test_cmd_whoami_backend_401_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autograde_idp import cli

    def boom(_api, _tok):
        raise notas.HttpError(401, '{"error":"invalid_token"}')

    monkeypatch.setattr(cli, "me_identity_call", boom)
    rc = cli.main(["whoami"])
    assert rc == 2


def test_cmd_whoami_network_error_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autograde_idp import cli

    def boom(_api, _tok):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(cli, "me_identity_call", boom)
    rc = cli.main(["whoami"])
    err = capsys.readouterr().err.lower()
    assert rc == 3
    assert "rede" in err


def test_cmd_whoami_token_expired_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    cli_with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """US-10 manual fix: cobre branch TokenExpiredError em cmd_whoami (cli.py:91-93)."""
    from autograde_idp import cli

    def expired(_bundle, *_a, **_k):
        raise TokenExpiredError("refresh token inválido/expirado: invalid_grant")

    monkeypatch.setattr(cli, "ensure_fresh_token", expired)
    rc = cli.main(["whoami"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "sessão expirada" in err
