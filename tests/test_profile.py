"""Testes do módulo autograde_idp.profile (US-01 — Fatia 5).

Cobre:
- GITHUB_USERNAME_RE (parametrizado, >=8 cases)
- fetch_me_identity (happy + erros HTTP/rede)
- post_me_profile (happy + erros HTTP)
- prompt_github_username (happy, retry após inválido, prefix '@')
- is_interactive (monkeypatch dos isatty)
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from autograde_idp import profile
from autograde_idp.notas import HttpError

# ---------------------------------------------------------------------------
# AC2 — GITHUB_USERNAME_RE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "username,expected",
    [
        ("foo", True),
        ("foo-bar", True),
        ("a", True),
        ("a-b-c", True),
        ("a" * 39, True),
        ("foo--bar", False),
        ("-foo", False),
        ("foo-", False),
        ("foo bar", False),
        ("", False),
        ("a" * 40, False),
    ],
)
def test_github_username_regex(username: str, expected: bool) -> None:
    assert bool(profile.GITHUB_USERNAME_RE.match(username)) is expected


# ---------------------------------------------------------------------------
# AC3 — fetch_me_identity
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


def test_fetch_me_identity_happy_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResp(
            200,
            {
                "email": "x@y.com",
                "nome": "X",
                "turma": "TD-2026-01",
                "github_username": "foo",
            },
        )

    monkeypatch.setattr(profile.requests, "get", fake_get)
    payload = profile.fetch_me_identity("http://test.local", "tok")
    assert payload["github_username"] == "foo"
    assert captured["url"] == "http://test.local/me/identity"
    assert captured["headers"] == {"Authorization": "Bearer tok"}


def test_fetch_me_identity_401_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(401, {"error": "invalid_token"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.fetch_me_identity("http://test.local", "tok")
    assert exc.value.status == 401
    assert "invalid_token" in exc.value.text


def test_fetch_me_identity_403_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(403, {"error": "not_in_roster"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.fetch_me_identity("http://test.local", "tok")
    assert exc.value.status == 403


def test_fetch_me_identity_500_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(500, {"error": "internal"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.fetch_me_identity("http://test.local", "tok")
    assert exc.value.status == 500


def test_fetch_me_identity_502_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "get",
        lambda url, headers=None, timeout=None: FakeResp(502, {"error": "bad_gateway"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.fetch_me_identity("http://test.local", "tok")
    assert exc.value.status == 502


def test_fetch_me_identity_network_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(profile.requests, "get", boom)
    with pytest.raises(requests.RequestException):
        profile.fetch_me_identity("http://test.local", "tok")


# ---------------------------------------------------------------------------
# AC4 — post_me_profile
# ---------------------------------------------------------------------------


def test_post_me_profile_happy_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp(200, {"updated": ["nome", "github_username"], "skipped": []})

    monkeypatch.setattr(profile.requests, "post", fake_post)
    payload = profile.post_me_profile("http://test.local", "tok", "Aluno X", "foo-bar")
    assert payload == {"updated": ["nome", "github_username"], "skipped": []}
    assert captured["url"] == "http://test.local/me/profile"
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    assert captured["json"] == {"nome": "Aluno X", "github_username": "foo-bar"}


def test_post_me_profile_400_invalid_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "post",
        lambda *a, **k: FakeResp(400, {"error": "invalid_github_username"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.post_me_profile("http://test.local", "tok", "X", "foo--bar")
    assert exc.value.status == 400
    assert "invalid_github_username" in exc.value.text


def test_post_me_profile_403_not_in_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "post",
        lambda *a, **k: FakeResp(403, {"error": "not_in_roster"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.post_me_profile("http://test.local", "tok", "X", "foo")
    assert exc.value.status == 403


def test_post_me_profile_500_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profile.requests,
        "post",
        lambda *a, **k: FakeResp(500, {"error": "internal"}),
    )
    with pytest.raises(HttpError) as exc:
        profile.post_me_profile("http://test.local", "tok", "X", "foo")
    assert exc.value.status == 500


# ---------------------------------------------------------------------------
# AC5 — prompt_github_username
# ---------------------------------------------------------------------------


def test_prompt_github_username_retries_until_valid_and_confirmed() -> None:
    """Invalid 'foo bar' + 'foo--bar' → re-prompt; 'foo' + confirm 's' → returns 'foo'."""
    inputs = iter(["foo bar", "foo--bar", "foo", "s"])
    prints: list[str] = []
    result = profile.prompt_github_username(
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda msg: prints.append(msg),
    )
    assert result == "foo"
    # Pelo menos 2 mensagens de erro foram impressas (uma por entrada inválida).
    assert sum("inválido" in p for p in prints) >= 2


def test_prompt_github_username_strips_leading_at() -> None:
    inputs = iter(["@bar", "s"])
    result = profile.prompt_github_username(
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda _msg: None,
    )
    assert result == "bar"


def test_prompt_github_username_confirm_no_reprompts() -> None:
    """Confirmação vazia/N volta ao prompt inicial."""
    inputs = iter(["foo", "", "bar", "sim"])
    result = profile.prompt_github_username(
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda _msg: None,
    )
    assert result == "bar"


# ---------------------------------------------------------------------------
# AC6 — is_interactive
# ---------------------------------------------------------------------------


def test_is_interactive_true_when_both_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(profile.sys.stdout, "isatty", lambda: True)
    assert profile.is_interactive() is True


def test_is_interactive_false_when_stdin_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(profile.sys.stdout, "isatty", lambda: True)
    assert profile.is_interactive() is False


def test_is_interactive_false_when_stdout_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(profile.sys.stdout, "isatty", lambda: False)
    assert profile.is_interactive() is False


# ---------------------------------------------------------------------------
# AC1 — exports
# ---------------------------------------------------------------------------


def test_module_exports_public_api() -> None:
    for name in (
        "GITHUB_USERNAME_RE",
        "fetch_me_identity",
        "post_me_profile",
        "prompt_github_username",
        "is_interactive",
    ):
        assert hasattr(profile, name), f"profile missing public symbol: {name}"
