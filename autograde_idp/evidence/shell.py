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

STDOUT_MAX_CHARS = 4096
DEFAULT_TIMEOUT_SECONDS = 15
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
    if shutil.which(binary) is None:
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

    try:
        proc = subprocess.run(
            command.cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
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


def commands_for_exercise(
    exercise_id: str, repo_url: Optional[str]
) -> List[ShellCommand]:
    """Lista hardcoded de comandos relevantes para cada exercício.

    Atualmente apenas ``1.2`` precisa de evidências shell (gh CLI).
    """
    if exercise_id == "1.2":
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
