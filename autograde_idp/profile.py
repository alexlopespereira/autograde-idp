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
from typing import Any, Callable, Optional

import requests

from autograde_idp import erros
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
    raise HttpError(resp.status_code, erros.truncar_corpo(resp.text or ""))


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
    raise HttpError(resp.status_code, erros.truncar_corpo(resp.text or ""))


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


def run_perfil(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    err_print: Optional[Callable[[str], None]] = None,
) -> int:
    """`autograde perfil` — mostra o cadastro e completa o github_username.

    Antes só existia a janela do primeiro `autograde login`: se o aluno
    pulasse (terminal sem TTY) ou errasse o username, não havia comando
    nenhum para ver ou corrigir — ele descobria o problema como um
    `repo_owner_mismatch` opaco na hora de validar. O backend continua
    recusando sobrescrever célula preenchida (proteção anti-hijacking), então
    o caso "já preenchido e errado" vira uma instrução explícita em vez de
    uma falha silenciosa.
    """
    from autograde_idp import erros
    from autograde_idp.auth import (
        AuthError,
        TokenAgeExceededError,
        TokenExpiredError,
        decode_id_token_unverified,
        ensure_fresh_token,
        load_token,
    )
    from autograde_idp.notas import api_url

    if err_print is None:

        def err_print(s: str) -> None:  # type: ignore[misc]
            print(s, file=sys.stderr)

    api = api_url()
    try:
        bundle = load_token()
        if bundle is None:
            err_print("Sem sessão ativa. Rode `autograde login`.")
            return 2
        bundle = ensure_fresh_token(bundle, api)
    except (TokenAgeExceededError, TokenExpiredError, AuthError) as exc:
        err_print(f"{exc}" + "\n" + "Rode `autograde login`.")
        return 2

    try:
        identity = fetch_me_identity(api, bundle.id_token)
    except requests.RequestException as exc:
        err_print(erros.explicar_rede(exc, acao="Ler seu cadastro"))
        return 3
    except HttpError as exc:
        err_print(erros.explicar_http(exc.status, exc.text, acao="Ler seu cadastro"))
        return 2 if exc.status in (401, 403) else 3

    turmas = identity.get("turmas") or [identity.get("turma", "?")]
    gh_atual = str(identity.get("github_username", "") or "")
    print_fn("Seu cadastro no roster:")
    print_fn(f"  email  : {identity.get('email', '?')}")
    print_fn(f"  nome   : {identity.get('nome', '?')}")
    print_fn(f"  turma  : {', '.join(str(t) for t in turmas)}")
    print_fn(f"  github : {gh_atual or '(não cadastrado)'}")

    if gh_atual:
        print_fn("")
        print_fn(
            "\n".join(
                [
                    "  O username do GitHub já está preenchido e só pode ser",
                    "  alterado pelo professor — a planilha não deixa um aluno",
                    "  sobrescrever o cadastro de outro. Se estiver errado, peça",
                    "  a correção citando o username certo.",
                ]
            )
        )
        return 0

    if not is_interactive():
        err_print(
            "Seu github_username está vazio e este terminal não é interativo."
            + "\n"
            + "Rode `autograde perfil` num terminal normal para preenchê-lo."
        )
        return 2

    print_fn("")
    print_fn("Seu github_username está vazio — sem ele o autograder não")
    print_fn("consegue confirmar que o repositório do exercício é seu.")
    try:
        payload = decode_id_token_unverified(bundle.id_token)
        nome = str(payload.get("name", "") or identity.get("nome", "") or "")
    except AuthError:
        nome = str(identity.get("nome", "") or "")
    gh = prompt_github_username(input_fn=input_fn, print_fn=print_fn)
    try:
        post_me_profile(api, bundle.id_token, nome, gh)
    except requests.RequestException as exc:
        err_print(erros.explicar_rede(exc, acao="Salvar seu cadastro"))
        return 3
    except HttpError as exc:
        err_print(erros.explicar_http(exc.status, exc.text, acao="Salvar seu cadastro"))
        return 3
    print_fn(f"Pronto: github={gh}")
    return 0
