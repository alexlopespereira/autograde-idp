"""Testes do módulo autograde_idp.auth (Device Flow + refresh + age check)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import responses

from autograde_idp.auth import (
    DEVICE_CODE_URL,
    REFRESH_LEEWAY_SECONDS,
    TOKEN_AGE_LIMIT_DAYS,
    TOKEN_URL,
    AuthError,
    TokenAgeExceededError,
    TokenBundle,
    TokenExpiredError,
    decode_id_token_unverified,
    device_login,
    ensure_fresh_token,
    load_oauth_credentials,
    load_token,
    refresh_access_token,
    save_token,
)

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-secret"  # noqa: S105 - fixture


def _make_bundle(**overrides) -> TokenBundle:
    base = dict(
        access_token="at-1",
        refresh_token="rt-1",
        id_token="header.eyJlbWFpbCI6ImFAYi5jb20iLCJuYW1lIjoiQSJ9.sig",
        expires_at=1000.0,
        first_login_at=500.0,
        client_id=CLIENT_ID,
    )
    base.update(overrides)
    return TokenBundle(**base)


@responses.activate
def test_device_login_happy_path():
    responses.add(
        responses.POST,
        DEVICE_CODE_URL,
        json={
            "device_code": "dc-abc",
            "user_code": "USER-CODE",
            "verification_url": "https://www.google.com/device",
            "expires_in": 1800,
            "interval": 0,
        },
        status=200,
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "at-real",
            "refresh_token": "rt-real",
            "id_token": "header.eyJlbWFpbCI6ImFAYi5jb20ifQ.sig",
            "expires_in": 3600,
        },
        status=200,
    )

    clock = [1_000_000.0]
    bundle = device_login(
        CLIENT_ID,
        CLIENT_SECRET,
        poll_sleep=lambda _s: None,
        now=lambda: clock[0],
    )
    assert bundle.access_token == "at-real"
    assert bundle.refresh_token == "rt-real"
    assert bundle.client_id == CLIENT_ID
    assert bundle.expires_at == pytest.approx(1_000_000.0 + 3600)
    assert bundle.first_login_at == pytest.approx(1_000_000.0)


@responses.activate
def test_device_login_polling_authorization_pending():
    responses.add(
        responses.POST,
        DEVICE_CODE_URL,
        json={
            "device_code": "dc",
            "user_code": "X",
            "verification_url": "u",
            "expires_in": 60,
            "interval": 0,
        },
        status=200,
    )
    responses.add(responses.POST, TOKEN_URL, json={"error": "authorization_pending"}, status=428)
    responses.add(responses.POST, TOKEN_URL, json={"error": "slow_down"}, status=428)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "at-final",
            "refresh_token": "rt-final",
            "id_token": "h.eyJlbWFpbCI6ImFAYi5jb20ifQ.s",
            "expires_in": 3600,
        },
        status=200,
    )

    captured: list[dict] = []
    bundle = device_login(
        CLIENT_ID,
        CLIENT_SECRET,
        poll_sleep=lambda _s: None,
        now=lambda: 0.0,
        on_user_code=captured.append,
    )
    assert bundle.access_token == "at-final"
    assert captured and captured[0]["user_code"] == "X"


@responses.activate
def test_device_login_access_denied_raises():
    responses.add(
        responses.POST,
        DEVICE_CODE_URL,
        json={
            "device_code": "dc",
            "user_code": "X",
            "verification_url": "u",
            "expires_in": 60,
            "interval": 0,
        },
        status=200,
    )
    responses.add(responses.POST, TOKEN_URL, json={"error": "access_denied"}, status=400)
    with pytest.raises(AuthError, match="negado"):
        device_login(CLIENT_ID, CLIENT_SECRET, poll_sleep=lambda _s: None, now=lambda: 0.0)


@responses.activate
def test_refresh_access_token_happy_path():
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "at-new",
            "id_token": "h.eyJlbWFpbCI6ImFAYi5jb20ifQ.s",
            "expires_in": 3600,
        },
        status=200,
    )
    bundle = _make_bundle(expires_at=0.0)
    refreshed = refresh_access_token(bundle, CLIENT_SECRET, now=lambda: 2000.0)
    assert refreshed.access_token == "at-new"
    assert refreshed.refresh_token == "rt-1"  # preservado se omitido na resposta
    assert refreshed.first_login_at == pytest.approx(500.0)
    assert refreshed.expires_at == pytest.approx(2000.0 + 3600)


@responses.activate
def test_refresh_invalid_grant_raises_token_expired():
    responses.add(responses.POST, TOKEN_URL, json={"error": "invalid_grant"}, status=400)
    with pytest.raises(TokenExpiredError):
        refresh_access_token(_make_bundle(), CLIENT_SECRET, now=lambda: 0.0)


def test_ensure_fresh_token_returns_unchanged_if_valid():
    bundle = _make_bundle(expires_at=10_000.0, first_login_at=9_000.0)
    out = ensure_fresh_token(
        bundle,
        CLIENT_SECRET,
        now=lambda: 9_500.0,
        persist=False,
    )
    assert out is bundle


@responses.activate
def test_ensure_fresh_token_refreshes_when_near_expiry(tmp_path):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "at-refreshed", "expires_in": 3600, "id_token": "h.e.s"},
        status=200,
    )
    bundle = _make_bundle(expires_at=1000.0, first_login_at=900.0)
    now_value = 1000.0 - (REFRESH_LEEWAY_SECONDS - 5)
    target = tmp_path / "token.json"
    refreshed = ensure_fresh_token(
        bundle,
        CLIENT_SECRET,
        now=lambda: now_value,
        persist=True,
        path=target,
    )
    assert refreshed.access_token == "at-refreshed"
    assert target.is_file()
    saved = json.loads(target.read_text())
    assert saved["access_token"] == "at-refreshed"


def test_ensure_fresh_token_raises_when_age_exceeded():
    bundle = _make_bundle(expires_at=10_000.0, first_login_at=0.0)
    future = (TOKEN_AGE_LIMIT_DAYS + 1) * 86400
    with pytest.raises(TokenAgeExceededError, match="5 meses"):
        ensure_fresh_token(bundle, CLIENT_SECRET, now=lambda: future, persist=False)


def test_save_and_load_token_roundtrip(tmp_path):
    bundle = _make_bundle()
    target = tmp_path / "token.json"
    save_token(bundle, path=target)
    loaded = load_token(target)
    assert loaded == bundle


def test_save_token_has_restricted_perms_on_unix(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Windows usa ACL default")
    bundle = _make_bundle()
    target = tmp_path / "token.json"
    save_token(bundle, path=target)
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600


def test_load_token_returns_none_when_missing(tmp_path):
    assert load_token(tmp_path / "nonexistent.json") is None


def test_load_token_raises_authError_when_json_corrupted(tmp_path):
    target = tmp_path / "token.json"
    target.write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(AuthError, match="corrompido"):
        load_token(target)


def test_load_token_raises_authError_when_field_missing(tmp_path):
    target = tmp_path / "token.json"
    target.write_text(json.dumps({"access_token": "x"}), encoding="utf-8")
    with pytest.raises(AuthError, match="corrompido"):
        load_token(target)


def test_decode_id_token_unverified_extracts_claims():
    # base64url("{"email":"a@b.com","name":"A"}") = "eyJlbWFpbCI6ImFAYi5jb20iLCJuYW1lIjoiQSJ9"
    jwt = "header.eyJlbWFpbCI6ImFAYi5jb20iLCJuYW1lIjoiQSJ9.signature"
    payload = decode_id_token_unverified(jwt)
    assert payload == {"email": "a@b.com", "name": "A"}


def test_decode_id_token_unverified_rejects_malformed():
    with pytest.raises(AuthError):
        decode_id_token_unverified("not-a-jwt")


def test_load_oauth_credentials_prefers_env_vars(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")
    cid, sec = load_oauth_credentials(env_file=Path("/nonexistent"))
    assert (cid, sec) == ("env-id", "env-secret")


def test_load_oauth_credentials_falls_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "# comment\nGOOGLE_OAUTH_CLIENT_ID=file-id\nGOOGLE_OAUTH_CLIENT_SECRET=file-sec\n",
        encoding="utf-8",
    )
    cid, sec = load_oauth_credentials(env_file=env_file)
    assert (cid, sec) == ("file-id", "file-sec")


def test_load_oauth_credentials_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(AuthError):
        load_oauth_credentials(env_file=tmp_path / "missing")
