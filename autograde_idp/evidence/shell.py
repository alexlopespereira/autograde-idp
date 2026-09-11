"""Coleta de evidências locais via shell (US-13).

Executa comandos como ``gh --version``, ``gh auth status`` e ``gh repo view``
via :func:`subprocess.run` com ``shell=False`` (sem injection), captura
``stdout``/``exit_code``/``captured_at`` e devolve estruturas serializáveis
para envio ao backend nos campos ``shell_evidence`` de ``/grade-preview`` e
``/submissions``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autograde_idp.curso import CURSO_DEFAULT, qualify_exercise_id

STDOUT_MAX_CHARS = 4096
DEFAULT_TIMEOUT_SECONDS = 15
# Teto para o `timeout:` que o YAML pede — o aluno espera na frente do terminal.
MAX_TIMEOUT_SECONDS = 300
GH_NOT_FOUND_MESSAGE = "gh not found in PATH"
GH_NOT_FOUND_EXIT_CODE = -1

_HTTPS_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/?#\s]+?)(?:\.git)?/?$"
)
_SSH_PATTERN = re.compile(
    r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$"
)


def _parse_owner_repo(repo_url: str) -> Optional[str]:
    """Normaliza URL GitHub em ``owner/repo``; ``None`` se irreconhecível."""
    if not isinstance(repo_url, str):
        return None
    candidate = repo_url.strip()
    if not candidate:
        return None
    for pat in (_HTTPS_PATTERN, _SSH_PATTERN):
        m = pat.match(candidate)
        if m:
            return f"{m.group('owner')}/{m.group('repo')}"
    return None


@dataclass
class ShellCommand:
    tool: str
    cmd: List[str]
    extract: Optional[str] = None
    # Só o caminho YAML usa: `pytest` numa suíte real estoura os 15s que bastam
    # para um `gh --version`. None = usa o timeout do chamador.
    timeout: Optional[int] = None


@dataclass
class CommandResult:
    tool: str
    cmd_joined: str
    exit_code: int
    stdout: str
    captured_at: str
    truncated: bool = False
    extract: Optional[str] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "tool": self.tool,
            "cmd_joined": self.cmd_joined,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "captured_at": self.captured_at,
        }
        if self.truncated:
            d["truncated"] = True
        if self.extract is not None:
            d["extract"] = self.extract
        return d


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= STDOUT_MAX_CHARS:
        return text, False
    return text[:STDOUT_MAX_CHARS], True


def _run_one(command: ShellCommand, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    cmd_joined = " ".join(command.cmd)
    if not command.cmd:
        return CommandResult(
            tool=command.tool,
            cmd_joined="",
            exit_code=GH_NOT_FOUND_EXIT_CODE,
            stdout="empty command",
            captured_at=_now_iso_utc(),
            extract=command.extract,
        )

    binary = command.cmd[0]
    resolvido = shutil.which(binary)
    if resolvido is None:
        return CommandResult(
            tool=command.tool,
            cmd_joined=cmd_joined,
            exit_code=GH_NOT_FOUND_EXIT_CODE,
            stdout=f"{binary} not found in PATH"
            if binary != "gh"
            else GH_NOT_FOUND_MESSAGE,
            captured_at=_now_iso_utc(),
            extract=command.extract,
        )

    # Invocar pelo caminho resolvido, não pelo nome. Em Windows nativo o
    # `npm` é `npm.CMD`, e `subprocess.run(["npm", ...], shell=False)` estoura
    # FileNotFoundError porque CreateProcess só completa `.exe` — o
    # `shutil.which` acima acha, a execução não. Com o caminho absoluto os dois
    # concordam, e some a rejanela entre checar e executar.
    argv = [resolvido, *command.cmd[1:]]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            # Ver doctor._run: sem encoding explícito a saída do `gh` no
            # Windows vira mojibake — e essa saída é a evidência enviada ao
            # backend.
            encoding="utf-8",
            errors="replace",
        )
        raw_stdout = proc.stdout or ""
        if proc.stderr:
            raw_stdout = (raw_stdout + ("\n" if raw_stdout else "") + proc.stderr).strip()
        stdout, truncated = _truncate(raw_stdout)
        return CommandResult(
            tool=command.tool,
            cmd_joined=cmd_joined,
            exit_code=int(proc.returncode),
            stdout=stdout,
            captured_at=_now_iso_utc(),
            truncated=truncated,
            extract=command.extract,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            tool=command.tool,
            cmd_joined=cmd_joined,
            exit_code=GH_NOT_FOUND_EXIT_CODE,
            stdout=f"timeout after {timeout}s",
            captured_at=_now_iso_utc(),
            extract=command.extract,
        )
    except OSError as exc:
        return CommandResult(
            tool=command.tool,
            cmd_joined=cmd_joined,
            exit_code=GH_NOT_FOUND_EXIT_CODE,
            stdout=f"execution failed: {exc}",
            captured_at=_now_iso_utc(),
            extract=command.extract,
        )


def collect_shell_evidence(
    commands: List[ShellCommand],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[CommandResult]:
    """Executa cada comando em ordem e devolve a lista de resultados.

    Nunca levanta — falhas viram :class:`CommandResult` com ``exit_code=-1``.
    """
    return [_run_one(c, timeout=timeout) for c in commands]


# Ids que coletam a evidência básica de `gh` (versão, auth, repo view).
# Registrados QUALIFICADOS por curso — `1.2` (td, legado sem prefixo) e
# `ia-1.2` — e não por id base. Explícito de propósito: um `ia-4.1` futuro
# com conteúdo próprio não pode herdar por acidente a evidência do `4.1` de
# TD (que roda curl contra localhost).
_GH_BASIC_IDS = frozenset(
    qualify_exercise_id(curso, base)
    for curso in (CURSO_DEFAULT, "ia")
    for base in ("1.2", "1.3", "1.4")
)


def commands_for_exercise(
    exercise_id: str, repo_url: Optional[str]
) -> List[ShellCommand]:
    """Lista hardcoded de comandos relevantes para cada exercício.

    Exercícios 1.2, 1.3 e 1.4 (dos dois cursos) envolvem `gh` CLI → coletam
    mesma evidência (versão, auth, repo view). Espelha
    ``app/evidence/shell.py:_WHITELIST`` no backend: comando coletado aqui que
    não esteja lá é rejeitado na submissão.
    """
    if exercise_id == "4.1":
        # API REST de TODO list avaliada por execução real: inputs FIXOS (o
        # store começa vazio → o primeiro POST cria id=1, tornando GET/PUT
        # determinísticos). Os `extract` casam com `args.extract` no YAML 4.1.
        base = "http://localhost:8000"
        return [
            ShellCommand(
                tool="shell", cmd=["curl", "-s", f"{base}/health"], extract="health"
            ),
            ShellCommand(
                tool="shell",
                cmd=[
                    "curl",
                    "-s",
                    "-X",
                    "POST",
                    f"{base}/tarefas",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    '{"titulo":"estudar APIs"}',
                ],
                extract="post_tarefa",
            ),
            ShellCommand(
                tool="shell", cmd=["curl", "-s", f"{base}/tarefas/1"], extract="get_tarefa"
            ),
            ShellCommand(
                tool="shell",
                cmd=[
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    f"{base}/tarefas/1",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    '{"titulo":"estudar APIs REST","concluida":true}',
                ],
                extract="put_tarefa",
            ),
        ]

    if exercise_id == "4.2":
        # MCP server local exercitado pelo cliente de teste do aluno, rodado no
        # cwd do repo (onde estão servidor_mcp.py e cliente_teste.py). Imprime o
        # envelope JSON validado pelo backend (extract=mcp_test).
        return [
            ShellCommand(
                tool="shell", cmd=["python", "cliente_teste.py"], extract="mcp_test"
            ),
        ]

    if exercise_id in _GH_BASIC_IDS:
        cmds: List[ShellCommand] = [
            ShellCommand(tool="shell", cmd=["gh", "--version"], extract="gh_version"),
            ShellCommand(tool="shell", cmd=["gh", "auth", "status"], extract="gh_auth"),
        ]
        owner_repo = _parse_owner_repo(repo_url) if repo_url else None
        if owner_repo:
            cmds.append(
                ShellCommand(
                    tool="shell",
                    cmd=[
                        "gh",
                        "repo",
                        "view",
                        owner_repo,
                        "--json",
                        "visibility,name,isPrivate",
                    ],
                    extract="gh_repo_view",
                )
            )
        return cmds
    return []


def collect_for_exercise(
    exercise_id: str,
    repo_url: Optional[str],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[CommandResult]:
    """Coleta evidências shell aplicáveis ao ``exercise_id`` informado."""
    return collect_shell_evidence(
        commands_for_exercise(exercise_id, repo_url), timeout=timeout
    )


# Binários que o CLI aceita executar a partir do YAML do exercício.
#
# O YAML vem da internet (raw.githubusercontent) e manda em `subprocess.run`
# na máquina do aluno. HTTPS mais o repositório do curso já são a barreira
# principal, mas uma allowlist fecha a porta para o cenário em que o repo de
# exercícios é comprometido: dá para pedir `pytest`, não `rm`. Comando fora da
# lista é ignorado com um CommandResult explicativo — nunca executado.
YAML_BINARIOS_PERMITIDOS = frozenset(
    {
        "gh",
        "git",
        "python",
        "python3",
        "py",
        "pytest",
        "curl",
        "node",
        "npm",
    }
)
BINARIO_BLOQUEADO_EXIT_CODE = -2


def _bloqueado(cmd: List[str], extract: Optional[str]) -> CommandResult:
    binario = cmd[0] if cmd else "(vazio)"
    return CommandResult(
        tool="shell",
        cmd_joined=" ".join(cmd),
        exit_code=BINARIO_BLOQUEADO_EXIT_CODE,
        stdout=(
            f"comando '{binario}' nao esta na allowlist do CLI e nao foi executado "
            f"(permitidos: {', '.join(sorted(YAML_BINARIOS_PERMITIDOS))})"
        ),
        captured_at=_now_iso_utc(),
        extract=extract,
    )


def commands_from_yaml(
    spec: Optional[Dict[str, Any]], repo_url: Optional[str]
) -> List[ShellCommand]:
    """Lê a seção ``comandos_shell:`` do YAML do exercício.

    Cada entrada é ``["gh", "--version"]`` ou
    ``{cmd: [...], extract: "pytest"}``. O placeholder ``{owner_repo}`` é
    substituído pelo ``owner/repo`` derivado do remote — o mesmo que o backend
    faz ao montar a whitelist, senão a evidência é rejeitada na submissão.

    Entrada malformada é ignorada; a validação dura é do backend.
    """
    if not isinstance(spec, dict):
        return []
    raw = spec.get("comandos_shell")
    if not isinstance(raw, list):
        return []
    owner_repo = _parse_owner_repo(repo_url) if repo_url else None
    out: List[ShellCommand] = []
    for entry in raw:
        timeout_override: Optional[int] = None
        if isinstance(entry, dict):
            cmd_raw = entry.get("cmd")
            extract = str(entry.get("extract") or "").strip() or None
            try:
                bruto = int(entry.get("timeout", 0) or 0)
            except (TypeError, ValueError):
                bruto = 0
            if bruto > 0:
                timeout_override = min(bruto, MAX_TIMEOUT_SECONDS)
        else:
            cmd_raw = entry
            extract = None
        if not isinstance(cmd_raw, list) or not cmd_raw:
            continue
        tokens = [str(tok) for tok in cmd_raw]
        if any(not tok for tok in tokens):
            continue
        if any("{owner_repo}" in tok for tok in tokens):
            if not owner_repo:
                # Sem remote reconhecível não dá para montar o comando; pular é
                # melhor que mandar "{owner_repo}" literal e tomar 400.
                continue
            tokens = [tok.replace("{owner_repo}", owner_repo) for tok in tokens]
        out.append(
            ShellCommand(
                tool="shell", cmd=tokens, extract=extract, timeout=timeout_override
            )
        )
    return out


def collect_for_exercise_spec(
    exercise_id: str,
    repo_url: Optional[str],
    spec: Optional[Dict[str, Any]] = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[CommandResult]:
    """Coleta a evidência shell do YAML; cai na lista hardcoded se não houver.

    Comando com binário fora da allowlist vira resultado explicativo sem ser
    executado — o aluno vê o motivo no boletim em vez de um critério mudo.
    """
    commands = commands_from_yaml(spec, repo_url)
    if not commands:
        return collect_shell_evidence(
            commands_for_exercise(exercise_id, repo_url), timeout=timeout
        )
    out: List[CommandResult] = []
    for command in commands:
        if command.cmd and command.cmd[0] not in YAML_BINARIOS_PERMITIDOS:
            out.append(_bloqueado(command.cmd, command.extract))
            continue
        out.append(_run_one(command, timeout=command.timeout or timeout))
    return out
