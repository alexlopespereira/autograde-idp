"""OAuth 2.0 Device Authorization Grant (RFC 8628) for Google.

Implementação direta via requests (sem google-auth-oauthlib) para manter
o CLI leve. Persiste refresh token em ~/.git-exercicios/token.json com
permissões restritas e força re-login se a sessão passar de 150 dias (R5).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint
SCOPE = "openid email profile"
REFRESH_LEEWAY_SECONDS = 60
TOKEN_AGE_LIMIT_DAYS = 150
CONFIG_DIR_NAME = ".git-exercicios"
TOKEN_FILENAME = "token.json"


class AuthError(Exception):
    """Erro de autenticação OAuth."""


class TokenExpiredError(AuthError):
    """Refresh token vencido — necessário novo login."""


class TokenAgeExceededError(AuthError):
    """Sessão tem mais de 150 dias — R5 force re-login."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    id_token: str
    expires_at: float
    first_login_at: float
    client_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenBundle":
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            id_token=str(data["id_token"]),
            expires_at=float(data["expires_at"]),
            first_login_at=float(data["first_login_at"]),
            client_id=str(data["client_id"]),
        )


def config_dir() -> Path:
    """Retorna ~/.git-exercicios criando se necessário."""
    path = Path.home() / CONFIG_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def token_path() -> Path:
    return config_dir() / TOKEN_FILENAME


def _read_env_local(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        return {}
    data: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip()
    return data


def load_oauth_credentials(env_file: Optional[Path] = None) -> tuple[str, str]:
    """Resolve client_id e client_secret a partir de env vars ou .env.local.

    Prioridade: env vars > env_file fornecido > autograde/.env.local relativo
    ao package (apenas em dev).
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    candidates: list[Path] = []
    if env_file is not None:
        candidates.append(env_file)
    else:
        here = Path(__file__).resolve()
        candidates.append(here.parents[2] / ".env.local")

    for candidate in candidates:
        env = _read_env_local(candidate)
        client_id = client_id or env.get("GOOGLE_OAUTH_CLIENT_ID")
        client_secret = client_secret or env.get("GOOGLE_OAUTH_CLIENT_SECRET")
        if client_id and client_secret:
            return client_id, client_secret

    if not client_id:
        raise AuthError("GOOGLE_OAUTH_CLIENT_ID não configurado (env var ou .env.local)")
    if not client_secret:
        raise AuthError("GOOGLE_OAUTH_CLIENT_SECRET não configurado (env var ou .env.local)")
    return client_id, client_secret


def save_token(bundle: TokenBundle, path: Optional[Path] = None) -> Path:
    """Persiste token em disco com permissão 0600 (Unix)."""
    target = path or token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle.to_dict(), indent=2), encoding="utf-8")
    if sys.platform != "win32":
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    return target


def load_token(path: Optional[Path] = None) -> Optional[TokenBundle]:
    target = path or token_path()
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"token.json corrompido: {exc}") from exc
    try:
        return TokenBundle.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError(f"token.json corrompido: campo inválido ({exc})") from exc


def _now() -> float:
    return time.time()


def device_login(
    client_id: str,
    client_secret: str,
    *,
    scope: str = SCOPE,
    poll_sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = _now,
    on_user_code: Optional[Callable[[dict[str, Any]], None]] = None,
) -> TokenBundle:
    """Executa o Device Authorization Grant completo.

    Polling respeita `interval` retornado pelo /device/code; trata
    authorization_pending e slow_down conforme RFC 8628 §3.5.
    """
    resp = requests.post(
        DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": scope},
        timeout=15,
    )
    if resp.status_code != 200:
        raise AuthError(f"device/code falhou: {resp.status_code} {resp.text}")
    device = resp.json()
    device_code = device["device_code"]
    interval = float(device.get("interval", 5))
    expires_in = float(device.get("expires_in", 1800))
    deadline = now() + expires_in

    if on_user_code is not None:
        on_user_code(device)
    else:
        url = device.get("verification_url")
        code = device.get("user_code")
        print(f"Acesse {url} e digite o código: {code}")

    while now() < deadline:
        poll_sleep(interval)
        token_resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=15,
        )
        body = token_resp.json() if token_resp.content else {}
        if token_resp.status_code == 200:
            return _build_bundle_from_token_response(body, client_id, now())
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "access_denied":
            raise AuthError("Login negado pelo usuário")
        if error == "expired_token":
            raise AuthError("Código expirou antes da confirmação. Tente novamente.")
        raise AuthError(f"token endpoint falhou: {token_resp.status_code} {body}")

    raise AuthError("Tempo esgotado aguardando confirmação no navegador")


def _build_bundle_from_token_response(
    body: dict[str, Any], client_id: str, login_time: float
) -> TokenBundle:
    try:
        return TokenBundle(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            id_token=str(body["id_token"]),
            expires_at=login_time + float(body.get("expires_in", 3600)),
            first_login_at=login_time,
            client_id=client_id,
        )
    except KeyError as exc:
        raise AuthError(f"resposta do token endpoint sem campo {exc}") from exc


def refresh_access_token(
    bundle: TokenBundle,
    client_secret: str,
    *,
    now: Callable[[], float] = _now,
) -> TokenBundle:
    """POST /token grant_type=refresh_token. Preserva first_login_at."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": bundle.client_id,
            "client_secret": client_secret,
            "refresh_token": bundle.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    body = resp.json() if resp.content else {}
    if resp.status_code != 200:
        error = body.get("error", "")
        if error in {"invalid_grant", "invalid_token"}:
            raise TokenExpiredError(f"refresh token inválido/expirado: {error}")
        raise AuthError(f"refresh falhou: {resp.status_code} {body}")
    return TokenBundle(
        access_token=str(body["access_token"]),
        refresh_token=str(body.get("refresh_token", bundle.refresh_token)),
        id_token=str(body.get("id_token", bundle.id_token)),
        expires_at=now() + float(body.get("expires_in", 3600)),
        first_login_at=bundle.first_login_at,
        client_id=bundle.client_id,
    )


def ensure_fresh_token(
    bundle: TokenBundle,
    client_secret: str,
    *,
    now: Callable[[], float] = _now,
    persist: bool = True,
    path: Optional[Path] = None,
) -> TokenBundle:
    """Garante access_token válido + valida idade da sessão (R5).

    Levanta TokenAgeExceededError se first_login_at < now - 150 dias.
    Renova access_token se faltar menos de REFRESH_LEEWAY_SECONDS pra expirar.
    """
    age_seconds = now() - bundle.first_login_at
    if age_seconds > TOKEN_AGE_LIMIT_DAYS * 86400:
        raise TokenAgeExceededError(
            "Sua sessão tem >5 meses, faça login novamente para evitar "
            "expiração silenciosa do Google"
        )
    if bundle.expires_at >= now() + REFRESH_LEEWAY_SECONDS:
        return bundle
    refreshed = refresh_access_token(bundle, client_secret, now=now)
    if persist:
        save_token(refreshed, path=path)
    return refreshed


def decode_id_token_unverified(id_token_jwt: str) -> dict[str, Any]:
    """Decodifica payload do ID token SEM verificar assinatura.

    Uso local-only: apenas para mostrar email/name ao usuário no whoami.
    Servidor backend faz a verificação real via JWKS.
    """
    parts = id_token_jwt.split(".")
    if len(parts) < 2:
        raise AuthError("id_token mal formado")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError(f"id_token inválido: {exc}") from exc


def token_age_days(bundle: TokenBundle, now: Callable[[], float] = _now) -> int:
    return int((now() - bundle.first_login_at) // 86400)
