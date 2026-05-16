"""Entry-point do CLI autograde.

Subcomandos:
- autograde --version
- autograde login
- autograde whoami
- autograde register
- autograde validar [exercicio_id] [--auto-submit]
- autograde notas
"""

from __future__ import annotations

# ruff: noqa: E402
# Suprime NotOpenSSLWarning do urllib3 — macOS system Python usa LibreSSL e
# emite warning toda vez que urllib3 importa. Mensagem é informativa
# (compatibilidade de biblioteca), não actionable pro aluno. O filterwarnings
# precisa rodar ANTES do `import requests` (que importa urllib3 transitivamente)
# pra interceptar o warning emitido no import-time de urllib3.
import argparse
import sys
import warnings
from typing import Optional

warnings.filterwarnings(
    "ignore",
    message=r".*OpenSSL.*",
    category=Warning,
)

import requests

from autograde_idp import __version__
from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenExpiredError,
    device_login,
    ensure_fresh_token,
    load_client_id,
    load_token,
    save_token,
    token_age_days,
    token_path,
)
from autograde_idp.notas import (
    HttpError as NotasHttpError,
)
from autograde_idp.notas import (
    api_url,
    me_identity_call,
    run_notas,
)
from autograde_idp.register import run_register
from autograde_idp.validar import run_validar


def _print_user_code(device: dict) -> None:
    url = device.get("verification_url") or device.get("verification_uri") or ""
    code = device.get("user_code", "")
    print("Autenticação Google (Device Flow)")
    print(f"  1. Abra: {url}")
    print(f"  2. Digite o código: {code}")
    print("  Aguardando confirmação...")


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"autograde {__version__} ({sys.platform})")
    return 0


def cmd_login(_args: argparse.Namespace) -> int:
    client_id = load_client_id()
    try:
        bundle = device_login(client_id, api_url(), on_user_code=_print_user_code)
    except AuthError as exc:
        print(f"erro de login: {exc}", file=sys.stderr)
        return 2
    save_token(bundle)
    print(f"Login OK. Token gravado em {token_path()}")
    return 0


def cmd_whoami(_args: argparse.Namespace) -> int:
    try:
        bundle = load_token()
    except AuthError as exc:
        print(f"erro ao ler token: {exc}", file=sys.stderr)
        return 2
    if bundle is None:
        print("Sem sessão ativa. Rode `autograde login`.", file=sys.stderr)
        return 2
    try:
        bundle = ensure_fresh_token(bundle, api_url())
    except TokenAgeExceededError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except TokenExpiredError as exc:
        print(f"sessão expirada: {exc}. Rode `autograde login` novamente.", file=sys.stderr)
        return 2
    except AuthError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    try:
        identity = me_identity_call(api_url(), bundle.id_token)
    except requests.RequestException as exc:
        print(f"erro de rede em /me/identity: {exc}", file=sys.stderr)
        return 3
    except NotasHttpError as exc:
        if exc.status == 401:
            print(f"token inválido: HTTP {exc.status} {exc.text}", file=sys.stderr)
            return 2
        if exc.status >= 500:
            print(f"/me/identity falhou: HTTP {exc.status} {exc.text}", file=sys.stderr)
            return 3
        print(f"/me/identity rejeitou: HTTP {exc.status} {exc.text}", file=sys.stderr)
        return 1
    email = identity.get("email", "?")
    name = identity.get("nome", "?")
    turma = identity.get("turma", "?")
    age = token_age_days(bundle)
    print(f"email: {email}")
    print(f"name : {name}")
    print(f"turma: {turma}")
    print(f"token_age_days: {age}")
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    return run_validar(
        exercise_id=args.exercicio_id,
        auto_submit=args.auto_submit,
    )


def cmd_notas(_args: argparse.Namespace) -> int:
    return run_notas()


def cmd_register(_args: argparse.Namespace) -> int:
    return run_register()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autograde",
        description="CLI cliente do autograder IDP-TD",
    )
    parser.add_argument("--version", action="store_true", help="mostra versão e plataforma")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="login via Google Device Flow")
    sub.add_parser("whoami", help="mostra usuário autenticado")
    sub.add_parser(
        "register",
        help="auto-registro: cadastra aluno fora do roster (turma + github_username)",
    )
    sub.add_parser("version", help="mostra versão e plataforma")
    val = sub.add_parser("validar", help="valida exercício e opcionalmente submete")
    val.add_argument(
        "exercicio_id",
        nargs="?",
        default=None,
        help="id do exercício (ex: 1.1); se omitido tenta detectar",
    )
    val.add_argument(
        "--auto-submit",
        action="store_true",
        help="pula o prompt s/n e submete automaticamente (uso CI/tests)",
    )
    sub.add_parser("notas", help="lista histórico de notas do aluno")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version or args.command == "version":
        return cmd_version(args)
    if args.command == "login":
        return cmd_login(args)
    if args.command == "whoami":
        return cmd_whoami(args)
    if args.command == "register":
        return cmd_register(args)
    if args.command == "validar":
        return cmd_validar(args)
    if args.command == "notas":
        return cmd_notas(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
