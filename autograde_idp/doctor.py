"""autograde doctor — checklist de pré-requisitos, com o conserto de cada item.

Existe para o aluno que não leu o tutorial. Em vez de descobrir os requisitos
um a um, por tentativa e erro (cada erro custando uma rodada de `validar`),
ele roda um comando e vê tudo de uma vez: Python, git, git identity, gh, gh
auth, sessão do autograde, roster/turma, e — se estiver dentro de um repo —
se o repo é dele e se está público.

Cada linha que falha vem com o comando exato que conserta. Nenhuma checagem
derruba a próxima: o objetivo é o diagnóstico COMPLETO numa passada só.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenExpiredError,
    ensure_fresh_token,
    load_token,
    token_age_days,
)
from autograde_idp.erros import AVISO, CERTO, ERRADO, FAQ_URL, SETA, sym
from autograde_idp.notas import HttpError, api_url, me_identity_call

OK = "ok"
WARN = "warn"
FAIL = "fail"

CMD_TIMEOUT = 15


@dataclass
class Check:
    nome: str
    status: str
    detalhe: str = ""
    conserto: tuple[str, ...] = ()


def _run(cmd: list[str]) -> tuple[int, str]:
    """Roda um comando e devolve (exit_code, saída). Nunca levanta."""
    if shutil.which(cmd[0]) is None:
        return 127, f"{cmd[0]} não encontrado no PATH"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CMD_TIMEOUT,
            shell=False,
            # `gh` e `git` emitem UTF-8 mesmo no Windows; sem isso o Python
            # decodifica com a codepage do console e a saída vira mojibake
            # ("âœ“ Logged in to..."). errors="replace" evita que um byte
            # inesperado derrube o diagnóstico inteiro.
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return proc.returncode, (out + ("\n" + err if err else "")).strip()


def check_python() -> Check:
    v = sys.version_info
    versao = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 9):
        return Check("Python 3.9+", OK, versao)
    return Check(
        "Python 3.9+",
        FAIL,
        f"{versao} é antigo demais",
        ("Instale Python 3.9 ou superior: https://www.python.org/downloads/",),
    )


def check_git() -> Check:
    code, out = _run(["git", "--version"])
    if code != 0:
        return Check(
            "git instalado",
            FAIL,
            out,
            (
                "Windows: baixe em https://git-scm.com/download/win e mantenha "
                "marcada a opção 'Git from the command line'.",
                "macOS: brew install git   |   Linux: sudo apt install git",
                "Depois abra um terminal NOVO (o PATH só atualiza em sessão nova).",
            ),
        )
    return Check("git instalado", OK, out.splitlines()[0])


def check_git_identity() -> Check:
    # `git config --get` sai com 1 e stdout vazio quando a chave não existe —
    # checar só o texto confundiria "não configurado" com "git nem instalado".
    code_nome, nome = _run(["git", "config", "--global", "user.name"])
    code_email, email = _run(["git", "config", "--global", "user.email"])
    if code_nome == 0 and code_email == 0 and nome and email:
        return Check("git identity", OK, f"{nome} <{email}>")
    return Check(
        "git identity",
        FAIL,
        "user.name e/ou user.email não configurados — `git commit` vai falhar",
        (
            'git config --global user.name "Seu Nome"',
            'git config --global user.email "seu.email@aluno.idp.edu.br"',
        ),
    )


def check_gh() -> Check:
    code, out = _run(["gh", "--version"])
    if code != 0:
        return Check(
            "gh (GitHub CLI) instalado",
            FAIL,
            out,
            (
                "Windows: winget install --id GitHub.cli",
                "macOS: brew install gh   |   Linux: https://cli.github.com",
                "Sem `gh` você perde os critérios `gh_*` dos exercícios 1.2 em "
                "diante (até 40 pontos).",
            ),
        )
    return Check("gh (GitHub CLI) instalado", OK, out.splitlines()[0])


def check_gh_auth() -> Check:
    code, out = _run(["gh", "auth", "status"])
    if code == 127:
        return Check("gh autenticado", FAIL, out, ("Instale o `gh` primeiro.",))
    if code != 0:
        return Check(
            "gh autenticado",
            FAIL,
            out.splitlines()[0] if out else "não autenticado",
            (
                "gh auth login",
                "Responda: GitHub.com > HTTPS > Y > Login with a web browser.",
            ),
        )
    conta = ""
    for linha in out.splitlines():
        if "Logged in to" in linha:
            conta = linha.strip().lstrip("✓- ").strip()
            break
    return Check("gh autenticado", OK, conta or "autenticado")


def check_sessao(
    identity_fn: Callable[[str, str], dict] = me_identity_call,
) -> tuple[Check, Optional[dict]]:
    """Sessão do autograde + identidade no roster. Devolve (check, identity)."""
    try:
        bundle = load_token()
    except AuthError as exc:
        return Check("sessão autograde", FAIL, str(exc), ("autograde login",)), None
    if bundle is None:
        return (
            Check("sessão autograde", FAIL, "sem sessão ativa", ("autograde login",)),
            None,
        )
    try:
        bundle = ensure_fresh_token(bundle, api_url())
    except (TokenAgeExceededError, TokenExpiredError) as exc:
        return (
            Check("sessão autograde", FAIL, str(exc), ("autograde login",)),
            None,
        )
    except AuthError as exc:
        return Check("sessão autograde", FAIL, str(exc), ("autograde login",)), None

    try:
        identity = identity_fn(api_url(), bundle.id_token)
    except requests.RequestException as exc:
        return (
            Check(
                "sessão autograde",
                WARN,
                f"não consegui falar com o backend: {exc}",
                ("Confira sua conexão e rode `autograde doctor` de novo.",),
            ),
            None,
        )
    except HttpError as exc:
        from autograde_idp.erros import parse_error_body

        codigo, message = parse_error_body(exc.text)
        conserto = ("autograde login",) if codigo != "not_in_roster" else (
            "Rode `autograde login` e escolha o email INSTITUCIONAL.",
            "Se o email estiver certo, peça ao professor para incluí-lo no roster.",
        )
        return (
            Check(
                "sessão autograde",
                FAIL,
                message or f"HTTP {exc.status} {codigo or exc.text}",
                conserto,
            ),
            None,
        )
    idade = token_age_days(bundle)
    return (
        Check("sessão autograde", OK, f"token com {idade} dia(s)"),
        identity,
    )


def check_roster(identity: Optional[dict]) -> list[Check]:
    if identity is None:
        return []
    email = str(identity.get("email", "") or "?")
    turmas = identity.get("turmas") or (
        [identity["turma"]] if identity.get("turma") else []
    )
    gh_user = str(identity.get("github_username", "") or "")
    checks = [
        Check("email no roster", OK, email),
        Check(
            "turma(s)",
            OK if turmas else FAIL,
            ", ".join(str(t) for t in turmas) or "nenhuma",
            ()
            if turmas
            else ("Peça ao professor para preencher a coluna `turma` da sua linha.",),
        ),
    ]
    checks.append(
        Check(
            "github_username no roster",
            OK if gh_user else FAIL,
            gh_user or "vazio",
            ()
            if gh_user
            else (
                "Rode `autograde perfil` — ele pergunta seu username e grava no "
                "roster. Sem isso o backend não confirma que o repo é seu.",
            ),
        )
    )
    return checks


def check_repo(cwd: Optional[Path], identity: Optional[dict]) -> list[Check]:
    """Checagens que só fazem sentido dentro do diretório de um exercício."""
    code, url = _run(["git", "config", "--get", "remote.origin.url"])
    if code != 0 or not url:
        return [
            Check(
                "diretório do exercício",
                WARN,
                "você não está num repositório git com `origin`",
                (
                    "Isso só importa na hora de rodar `autograde validar`: entre "
                    "no diretório do repo do exercício antes.",
                ),
            )
        ]
    checks = [Check("diretório do exercício", OK, url)]

    owner = _owner_de(url)
    gh_user = str((identity or {}).get("github_username", "") or "")
    if owner and gh_user:
        bate = owner.lower() == gh_user.lower()
        checks.append(
            Check(
                "repo pertence a você",
                OK if bate else FAIL,
                f"{owner} (roster: {gh_user})",
                ()
                if bate
                else (
                    "Ou você está no diretório de outro repo, ou o roster tem o "
                    "username errado. Confira com `gh auth status`.",
                ),
            )
        )

    if owner:
        code, out = _run(
            ["gh", "repo", "view", _owner_repo(url) or "", "--json", "visibility"]
        )
        if code == 0 and '"PUBLIC"' in out:
            checks.append(Check("repo público", OK, "PUBLIC"))
        elif code == 0:
            checks.append(
                Check(
                    "repo público",
                    FAIL,
                    "o repositório está privado — o backend não consegue lê-lo",
                    (
                        "No GitHub: Settings > General > Danger Zone > Change "
                        "visibility > Public.",
                    ),
                )
            )
    return checks


def _owner_repo(url: str) -> Optional[str]:
    from autograde_idp.evidence.shell import _parse_owner_repo

    return _parse_owner_repo(url)


def _owner_de(url: str) -> Optional[str]:
    owner_repo = _owner_repo(url)
    return owner_repo.split("/", 1)[0] if owner_repo else None


def coletar(cwd: Optional[Path] = None) -> list[Check]:
    checks = [check_python(), check_git(), check_git_identity(), check_gh(), check_gh_auth()]
    sessao, identity = check_sessao()
    checks.append(sessao)
    checks.extend(check_roster(identity))
    checks.extend(check_repo(cwd, identity))
    return checks


def _marks() -> tuple[str, str, str]:
    return sym(CERTO), sym(AVISO), sym(ERRADO)


def render(checks: list[Check]) -> str:
    ok, warn, fail = _marks()
    largura = max((len(c.nome) for c in checks), default=0)
    linhas = ["Diagnóstico do ambiente:", ""]
    for c in checks:
        mark = {OK: ok, WARN: warn, FAIL: fail}[c.status]
        linhas.append(f"  {mark} {c.nome.ljust(largura)}  {c.detalhe}".rstrip())
        for passo in c.conserto:
            linhas.append(f"       {sym(SETA)} {passo}")
    problemas = [c for c in checks if c.status == FAIL]
    linhas.append("")
    if problemas:
        linhas.append(
            f"  {len(problemas)} item(ns) precisam de conserto antes de "
            "`autograde validar` funcionar."
        )
        linhas.append(f"  FAQ: {FAQ_URL}")
    else:
        linhas.append("  Tudo pronto. Rode `autograde validar <id>` no repo do exercício.")
    return "\n".join(linhas)


def run_doctor(
    *,
    cwd: Optional[Path] = None,
    print_fn: Callable[[str], None] = print,
) -> int:
    checks = coletar(cwd)
    print_fn(render(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0
