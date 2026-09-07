"""Entry-point do CLI autograde.

Subcomandos:
- autograde --version
- autograde login
- autograde whoami
- autograde validar [exercicio_id] [--auto-submit]   # ex: 1.1 (TD) | ia-1.1 (Agentes de IA)
- autograde notas
- autograde doctor
- autograde perfil
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


def _tolerar_console_legado() -> None:
    """Impede UnicodeEncodeError no console padrão do Windows (cp1252).

    Os símbolos "bonitos" já degradam para ASCII via ``erros.sym``, mas texto
    do backend (ou do próprio GitHub) pode trazer qualquer caractere. Um
    crash de encoding no meio de uma mensagem de erro deixaria o aluno sem a
    informação exatamente quando ela mais importa — melhor um '?' no lugar do
    caractere do que um traceback no lugar da mensagem.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

import requests

from autograde_idp import __version__, erros
from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenBundle,
    TokenExpiredError,
    decode_id_token_unverified,
    device_login,
    ensure_fresh_token,
    load_client_id,
    load_token,
    save_token,
    token_age_days,
    token_path,
)
from autograde_idp.doctor import run_doctor
from autograde_idp.notas import (
    HttpError as NotasHttpError,
)
from autograde_idp.notas import (
    api_url,
    me_identity_call,
    run_notas,
)
from autograde_idp.profile import (
    fetch_me_identity,
    is_interactive,
    post_me_profile,
    prompt_github_username,
    run_perfil,
)
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


def _complete_profile_if_needed(bundle: TokenBundle, api: str) -> None:
    """Best-effort: prompta github_username após login se ainda não estiver set.

    Não falha o login — qualquer exceção HTTP/rede é logada em stderr e a função
    retorna silenciosa. O caller (cmd_login) também envelopa em try/except
    catch-all pra garantir que o return code do login permanece 0.
    """
    if not is_interactive():
        print(
            "perfil incompleto (falta seu username do GitHub) e este terminal "
            "não é interativo. Rode `autograde perfil` num terminal normal — "
            "sem isso o autograder não consegue confirmar que o repositório do "
            "exercício é seu.",
            file=sys.stderr,
        )
        return
    try:
        identity = fetch_me_identity(api, bundle.id_token)
    except requests.RequestException as exc:
        print(
            f"não foi possível verificar perfil (erro de rede: {exc})",
            file=sys.stderr,
        )
        return
    except NotasHttpError as exc:
        print(
            f"não foi possível verificar perfil (status {exc.status})",
            file=sys.stderr,
        )
        return
    if identity.get("github_username"):
        return
    try:
        payload = decode_id_token_unverified(bundle.id_token)
        nome = str(payload.get("name", "") or "")
    except AuthError:
        nome = ""
    gh = prompt_github_username()
    try:
        post_me_profile(api, bundle.id_token, nome, gh)
    except NotasHttpError as exc:
        print(
            f"erro ao salvar perfil: HTTP {exc.status} {exc.text}. "
            "Re-execute `autograde login` pra retentar.",
            file=sys.stderr,
        )
        return
    print(f"Perfil completo: nome={nome} github={gh}")


def cmd_login(_args: argparse.Namespace) -> int:
    client_id = load_client_id()
    try:
        bundle = device_login(client_id, api_url(), on_user_code=_print_user_code)
    except AuthError as exc:
        print(f"erro de login: {exc}", file=sys.stderr)
        return 2
    save_token(bundle)
    print(f"Login OK. Token gravado em {token_path()}")
    try:
        _complete_profile_if_needed(bundle, api_url())
    except Exception as exc:
        print(f"perfil não atualizado: {exc}", file=sys.stderr)
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
        print(erros.explicar_rede(exc, acao="Identificar você"), file=sys.stderr)
        return 3
    except NotasHttpError as exc:
        print(
            erros.explicar_http(exc.status, exc.text, acao="Identificar você"),
            file=sys.stderr,
        )
        return 2 if exc.status in (401, 403) else 3
    email = identity.get("email", "?")
    name = identity.get("nome", "?")
    # `turmas` (lista) é o campo novo; `turma` continua vindo para não quebrar
    # CLI antiga contra backend novo — e vice-versa.
    turmas = identity.get("turmas") or [identity.get("turma", "?")]
    gh_user = identity.get("github_username", "")
    age = token_age_days(bundle)
    print(f"email: {email}")
    print(f"name : {name}")
    print(f"turma: {', '.join(str(t) for t in turmas)}")
    print(f"github: {gh_user or '(não cadastrado)'}")
    print(f"token_age_days: {age}")
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    return run_validar(
        exercise_id=args.exercicio_id,
        auto_submit=args.auto_submit,
    )


def cmd_notas(_args: argparse.Namespace) -> int:
    return run_notas()


def cmd_doctor(_args: argparse.Namespace) -> int:
    return run_doctor()


def cmd_perfil(_args: argparse.Namespace) -> int:
    return run_perfil()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autograde",
        description="CLI cliente do autograder IDP-TD",
    )
    parser.add_argument("--version", action="store_true", help="mostra versão e plataforma")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="login via Google Device Flow")
    sub.add_parser("whoami", help="mostra usuário autenticado")
    sub.add_parser("version", help="mostra versão e plataforma")
    val = sub.add_parser("validar", help="valida exercício e opcionalmente submete")
    val.add_argument(
        "exercicio_id",
        nargs="?",
        default=None,
        help="id do exercício (ex: 1.1 ou ia-1.1); se omitido tenta detectar",
    )
    val.add_argument(
        "--auto-submit",
        action="store_true",
        help="pula o prompt s/n e submete automaticamente (uso CI/tests)",
    )
    sub.add_parser("notas", help="lista histórico de notas do aluno")
    sub.add_parser(
        "perfil",
        help="mostra seu cadastro (email, turma, github) e completa o que faltar",
    )
    sub.add_parser(
        "doctor",
        help="checa pré-requisitos (python, git, gh, login, turma, repo) e diz como consertar",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    _tolerar_console_legado()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version or args.command == "version":
        return cmd_version(args)
    if args.command == "login":
        return cmd_login(args)
    if args.command == "whoami":
        return cmd_whoami(args)
    if args.command == "validar":
        return cmd_validar(args)
    if args.command == "notas":
        return cmd_notas(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "perfil":
        return cmd_perfil(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
