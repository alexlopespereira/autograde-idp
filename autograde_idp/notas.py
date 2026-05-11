"""autograde notas — lista histórico do aluno via GET /me/grades.

Também expõe `me_identity_call` usado por `whoami` para obter turma.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Callable, Optional

import requests

from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenBundle,
    TokenExpiredError,
    ensure_fresh_token,
    load_oauth_credentials,
    load_token,
)

DEFAULT_API_URL = "http://localhost:8080"
EMPTY_MESSAGE = "Você ainda não submeteu nenhum exercício."


class NotasError(Exception):
    """Erro durante autograde notas — mapeado para exit code != 0."""


class HttpError(NotasError):
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text
        super().__init__(f"HTTP {status}: {text}")


def api_url() -> str:
    return os.environ.get("AUTOGRADE_API_URL", DEFAULT_API_URL).rstrip("/")


def _get(api: str, path: str, token: str) -> dict[str, Any]:
    resp = requests.get(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            raise NotasError(f"resposta inválida de {path}: {exc}") from exc
    text = (resp.text or "")[:500]
    raise HttpError(resp.status_code, text)


def me_grades_call(api: str, token: str) -> dict[str, Any]:
    return _get(api, "/me/grades", token)


def me_identity_call(api: str, token: str) -> dict[str, Any]:
    return _get(api, "/me/identity", token)


def _load_fresh_bundle() -> TokenBundle:
    bundle = load_token()
    if bundle is None:
        raise NotasError("Sem sessão ativa. Rode `autograde login`.")
    _client_id, client_secret = load_oauth_credentials()
    return ensure_fresh_token(bundle, client_secret)


def _format_iso_local(ts: str) -> str:
    """Converte ISO-8601 (UTC) para string na timezone local do usuário."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()


def render_grades_table(grades: list[dict[str, Any]]) -> str:
    headers = ("Exercício", "Melhor Nota", "Tentativas", "Última Submissão")
    rows: list[tuple[str, str, str, str]] = []
    for g in grades:
        rows.append(
            (
                str(g.get("exercicio", "")),
                str(g.get("melhor_nota", "")),
                str(g.get("num_tentativas", "")),
                _format_iso_local(str(g.get("ultima_submissao_at", ""))),
            )
        )
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)

    def fmt(cols: tuple[str, ...]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))

    lines = [fmt(headers), "-+-".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt(row))
    return "\n".join(lines)


def run_notas(
    *,
    print_fn: Callable[[str], None] = print,
    err_print: Optional[Callable[[str], None]] = None,
) -> int:
    """Executa GET /me/grades e renderiza tabela. Retorna exit code."""
    if err_print is None:

        def err_print(s: str) -> None:  # type: ignore[misc]
            print(s, file=sys.stderr)

    try:
        bundle = _load_fresh_bundle()
    except TokenAgeExceededError as exc:
        err_print(str(exc))
        return 2
    except TokenExpiredError as exc:
        err_print(f"sessão expirada: {exc}. Rode `autograde login` novamente.")
        return 2
    except (AuthError, NotasError) as exc:
        err_print(f"erro: {exc}")
        return 2

    api = api_url()
    try:
        payload = me_grades_call(api, bundle.access_token)
    except requests.RequestException as exc:
        err_print(f"erro de rede em /me/grades: {exc}")
        return 3
    except HttpError as exc:
        if exc.status >= 500:
            err_print(f"/me/grades falhou: HTTP {exc.status} {exc.text}")
            return 3
        if exc.status == 401:
            err_print(f"token inválido: HTTP {exc.status} {exc.text}")
            return 2
        err_print(f"/me/grades rejeitou: HTTP {exc.status} {exc.text}")
        return 1
    except NotasError as exc:
        err_print(f"erro: {exc}")
        return 3

    grades = payload.get("grades") or []
    if not grades:
        print_fn(EMPTY_MESSAGE)
        return 0
    print_fn(render_grades_table(grades))
    return 0
