"""autograde profile — completar perfil no primeiro login.

Encapsula:
- GET  /me/identity (fetch_me_identity)
- POST /me/profile  (post_me_profile)
- Prompt + validação local do github_username (prompt_github_username)
- Detecção de TTY (is_interactive)

Reusa HttpError de autograde_idp.notas para preservar mapping de exit codes
existente. Módulo standalone — não importa cli.py.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Callable

import requests

from autograde_idp.notas import HttpError

# Regex v3 — IDÊNTICA à do backend app/endpoints.py:132 (PR #12).
# Username GitHub: 1-39 chars; alfanumérico + hífen; sem hífen no início, no fim,
# nem consecutivos (lookahead garante que '-' é sempre seguido de [a-zA-Z0-9]).
GITHUB_USERNAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$")

_GH_ERROR_MSG = (
    "username inválido — use alfanumérico + hífen, 1-39 chars, "
    "sem hífen no início/fim/consecutivo."
)


def fetch_me_identity(api: str, id_token: str) -> dict[str, Any]:
    """GET {api}/me/identity. Retorna payload JSON em 200, raise HttpError em != 200."""
    resp = requests.get(
        f"{api}/me/identity",
        headers={"Authorization": f"Bearer {id_token}"},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    raise HttpError(resp.status_code, (resp.text or "")[:500])


def post_me_profile(
    api: str, id_token: str, nome: str, github_username: str
) -> dict[str, Any]:
    """POST {api}/me/profile. Retorna payload JSON em 200, raise HttpError em != 200."""
    resp = requests.post(
        f"{api}/me/profile",
        headers={"Authorization": f"Bearer {id_token}"},
        json={"nome": nome, "github_username": github_username},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    raise HttpError(resp.status_code, (resp.text or "")[:500])


def prompt_github_username(
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> str:
    """Loop interativo até obter github_username válido + confirmado.

    - strip + lstrip('@') no input.
    - se não bate com GITHUB_USERNAME_RE → print erro amigável + repete.
    - após valido, prompt 'Confirmar '<gh>'? [s/N]: ' — 's'/'sim' (case-insensitive)
      retorna; qualquer outra resposta volta ao prompt inicial.
    """
    while True:
        raw = input_fn("Seu username do GitHub (sem @): ")
        candidate = raw.strip().lstrip("@")
        if not GITHUB_USERNAME_RE.match(candidate):
            print_fn(_GH_ERROR_MSG)
            continue
        confirm = input_fn(f"Confirmar '{candidate}'? [s/N]: ").strip().lower()
        if confirm in ("s", "sim"):
            return candidate


def is_interactive() -> bool:
    """True se stdin e stdout são ambos TTYs. Wrapper testável."""
    return sys.stdin.isatty() and sys.stdout.isatty()
