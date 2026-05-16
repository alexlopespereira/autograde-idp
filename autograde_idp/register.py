"""autograde register — auto-registro do aluno fora do roster.

Fluxo (fluxo emergencial — início da disciplina, roster ainda incompleto):

1. Aluno roda `autograde login` (Device Flow), obtem id_token Google.
2. `autograde register` chama GET /turmas pra exibir lista, prompta o aluno
   pra escolher a turma e digitar github_username.
3. POST /me/register no backend grava na Roster Sheet. Após isso, o aluno
   pode rodar `autograde validar` normalmente.

Erros mapeados:
- 409 already_registered: aluno já está no roster — siga pro validar.
- 400 invalid_turma / invalid_github_username: corrige input e retenta.
- 503 registration_disabled: prof não habilitou (TURMAS_DISPONIVEIS vazia).
"""
from __future__ import annotations

import re
import sys
from typing import Any, Callable, Optional

import requests

from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenBundle,
    TokenExpiredError,
    ensure_fresh_token,
    load_token,
)
from autograde_idp.notas import HttpError, NotasError, api_url

GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def _get_turmas(api: str, token: str) -> list[str]:
    resp = requests.get(
        f"{api}/turmas",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise HttpError(resp.status_code, (resp.text or "")[:500])
    try:
        return list(resp.json().get("turmas", []))
    except ValueError as exc:
        raise NotasError(f"resposta inválida de /turmas: {exc}") from exc


def _post_register(api: str, token: str, github_username: str, turma: str) -> dict[str, Any]:
    resp = requests.post(
        f"{api}/me/register",
        headers={"Authorization": f"Bearer {token}"},
        json={"github_username": github_username, "turma": turma},
        timeout=15,
    )
    if resp.status_code == 200:
        return resp.json()
    raise HttpError(resp.status_code, (resp.text or "")[:500])


def _load_fresh_bundle() -> TokenBundle:
    bundle = load_token()
    if bundle is None:
        raise NotasError("Sem sessão ativa. Rode `autograde login`.")
    return ensure_fresh_token(bundle, api_url())


def _prompt_turma(turmas: list[str], input_fn: Callable[[str], str]) -> str:
    if len(turmas) == 1:
        print(f"Turma única disponível: {turmas[0]}")
        return turmas[0]
    print("Turmas disponíveis:")
    for idx, t in enumerate(turmas, start=1):
        print(f"  {idx}) {t}")
    while True:
        choice = input_fn("Escolha o número da sua turma: ").strip()
        try:
            i = int(choice)
        except ValueError:
            print("Digite um número.")
            continue
        if 1 <= i <= len(turmas):
            return turmas[i - 1]
        print(f"Número fora de range (1-{len(turmas)}).")


def _prompt_github(input_fn: Callable[[str], str]) -> str:
    while True:
        gh = input_fn("Seu username do GitHub (sem @): ").strip().lstrip("@")
        if GITHUB_USERNAME_RE.match(gh):
            return gh
        print("Username inválido (alfanumérico + hífen, 1-39 chars, sem hífen no início/fim).")


def run_register(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    err_print: Optional[Callable[[str], None]] = None,
) -> int:
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
        turmas = _get_turmas(api, bundle.id_token)
    except requests.RequestException as exc:
        err_print(f"erro de rede em /turmas: {exc}")
        return 3
    except HttpError as exc:
        if exc.status == 401:
            err_print(f"token inválido: HTTP {exc.status} {exc.text}")
            return 2
        err_print(f"/turmas falhou: HTTP {exc.status} {exc.text}")
        return 3
    except NotasError as exc:
        err_print(f"erro: {exc}")
        return 3

    if not turmas:
        err_print(
            "Auto-registro indisponível: nenhuma turma configurada no backend."
            " Contate o professor."
        )
        return 1

    turma = _prompt_turma(turmas, input_fn)
    github_username = _prompt_github(input_fn)

    print_fn(f"Registrando: turma={turma}, github_username={github_username}")
    try:
        payload = _post_register(api, bundle.id_token, github_username, turma)
    except requests.RequestException as exc:
        err_print(f"erro de rede em /me/register: {exc}")
        return 3
    except HttpError as exc:
        if exc.status == 409:
            err_print(
                "Você já está cadastrado. Rode `autograde whoami` pra ver sua turma."
            )
            return 1
        if exc.status == 400:
            err_print(f"dados inválidos: HTTP {exc.status} {exc.text}")
            return 1
        if exc.status == 503:
            err_print(
                "Auto-registro desabilitado no backend (TURMAS_DISPONIVEIS vazia)."
                " Contate o professor."
            )
            return 1
        if exc.status == 401:
            err_print(f"token inválido: HTTP {exc.status} {exc.text}")
            return 2
        err_print(f"/me/register falhou: HTTP {exc.status} {exc.text}")
        return 3

    print_fn(
        f"Cadastro OK. email={payload.get('email')} nome={payload.get('nome')} "
        f"turma={payload.get('turma')} github={payload.get('github_username')}"
    )
    print_fn("Pronto pra rodar `autograde validar <exercicio>`.")
    return 0
