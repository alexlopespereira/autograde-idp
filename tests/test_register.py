"""Testes do comando autograde register (auto-registro)."""
from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from autograde_idp import register
from autograde_idp.auth import TokenBundle


@pytest.fixture
def fake_bundle() -> TokenBundle:
    return TokenBundle(
        access_token="at",
        refresh_token="rt",
        id_token="h.e.s",
        expires_at=1e12,
        first_login_at=1e9,
        client_id="cid",
    )


@pytest.fixture
def with_fresh_token(monkeypatch: pytest.MonkeyPatch, fake_bundle: TokenBundle):
    monkeypatch.setattr(register, "load_token", lambda: fake_bundle)
    monkeypatch.setattr(register, "ensure_fresh_token", lambda b, *_a, **_k: b)
    return fake_bundle


class FakeResp:
    def __init__(self, status: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self._body = body or {}
        self.text = text or json.dumps(self._body)

    def json(self) -> dict:
        return self._body


def _stub_requests(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_resp: FakeResp | Exception,
    post_resp: FakeResp | Exception | None = None,
) -> dict[str, Any]:
    """Stub requests.get/post. Captura kwargs pra asserções."""
    captured: dict[str, Any] = {"get": None, "post": None}

    def fake_get(url: str, **kwargs: Any) -> FakeResp:
        captured["get"] = {"url": url, **kwargs}
        if isinstance(get_resp, Exception):
            raise get_resp
        return get_resp

    def fake_post(url: str, **kwargs: Any) -> FakeResp:
        captured["post"] = {"url": url, **kwargs}
        if isinstance(post_resp, Exception):
            raise post_resp
        assert post_resp is not None
        return post_resp

    monkeypatch.setattr(register.requests, "get", fake_get)
    monkeypatch.setattr(register.requests, "post", fake_post)
    return captured


def _input_from(answers: list[str]):
    iterator = iter(answers)

    def _input(_prompt: str = "") -> str:
        return next(iterator)

    return _input


def test_run_register_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured = _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01", "TD-2026-02"]}),
        post_resp=FakeResp(
            200,
            {
                "email": "novato@dominio.edu",
                "nome": "Aluno Novato",
                "turma": "TD-2026-02",
                "github_username": "novato-gh",
            },
        ),
    )

    rc = register.run_register(input_fn=_input_from(["2", "novato-gh"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Cadastro OK" in out
    assert "novato-gh" in out
    assert "TD-2026-02" in out

    # POST body bate com escolha do usuário
    assert captured["post"]["url"].endswith("/me/register")
    assert captured["post"]["json"] == {
        "github_username": "novato-gh",
        "turma": "TD-2026-02",
    }


def test_run_register_single_turma_auto_selects(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured = _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01"]}),
        post_resp=FakeResp(
            200,
            {
                "email": "x@y.com",
                "nome": "X",
                "turma": "TD-2026-01",
                "github_username": "x-gh",
            },
        ),
    )

    rc = register.run_register(input_fn=_input_from(["x-gh"]))
    assert rc == 0
    assert captured["post"]["json"]["turma"] == "TD-2026-01"


def test_run_register_invalid_github_then_valid(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
) -> None:
    _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01"]}),
        post_resp=FakeResp(
            200,
            {
                "email": "x@y.com",
                "nome": "X",
                "turma": "TD-2026-01",
                "github_username": "valido-gh",
            },
        ),
    )
    # Primeiro input: inválido (espaço). Segundo: válido.
    rc = register.run_register(input_fn=_input_from(["bad name", "valido-gh"]))
    assert rc == 0


def test_run_register_strips_at_prefix_from_github_username(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
) -> None:
    captured = _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01"]}),
        post_resp=FakeResp(
            200,
            {
                "email": "x@y.com",
                "nome": "X",
                "turma": "TD-2026-01",
                "github_username": "valido-gh",
            },
        ),
    )
    rc = register.run_register(input_fn=_input_from(["@valido-gh"]))
    assert rc == 0
    assert captured["post"]["json"]["github_username"] == "valido-gh"


def test_run_register_no_turmas_configured_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_requests(monkeypatch, get_resp=FakeResp(200, {"turmas": []}))
    rc = register.run_register(input_fn=_input_from([]))
    assert rc == 1
    assert "indisponível" in capsys.readouterr().err.lower()


def test_run_register_already_registered_409(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01"]}),
        post_resp=FakeResp(409, {"error": "already_registered"}),
    )
    rc = register.run_register(input_fn=_input_from(["x-gh"]))
    assert rc == 1
    assert "já está cadastrado" in capsys.readouterr().err.lower()


def test_run_register_503_disabled_at_backend(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_requests(
        monkeypatch,
        get_resp=FakeResp(200, {"turmas": ["TD-2026-01"]}),
        post_resp=FakeResp(503, {"error": "registration_disabled"}),
    )
    rc = register.run_register(input_fn=_input_from(["x-gh"]))
    assert rc == 1
    assert "desabilitado" in capsys.readouterr().err.lower()


def test_run_register_network_error_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    with_fresh_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_requests(
        monkeypatch,
        get_resp=requests.ConnectionError("DNS fail"),
    )
    rc = register.run_register(input_fn=_input_from([]))
    assert rc == 3
    assert "rede" in capsys.readouterr().err.lower()


def test_run_register_no_token_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(register, "load_token", lambda: None)
    rc = register.run_register(input_fn=_input_from([]))
    assert rc == 2
    assert "login" in capsys.readouterr().err.lower()
