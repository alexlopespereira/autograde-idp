"""Testes do collector de evidência shell (US-13)."""
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

import pytest

from autograde_idp.evidence import shell as shell_mod
from autograde_idp.evidence.shell import (
    GH_NOT_FOUND_EXIT_CODE,
    GH_NOT_FOUND_MESSAGE,
    STDOUT_MAX_CHARS,
    CommandResult,
    ShellCommand,
    collect_for_exercise,
    collect_shell_evidence,
    commands_for_exercise,
)

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$")


class FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def gh_present(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Faz shutil.which('gh') retornar caminho fake e captura subprocess.run."""
    calls: List[Dict[str, Any]] = []

    def fake_which(binary: str) -> Optional[str]:
        return "/usr/local/bin/gh" if binary == "gh" else None

    def fake_run(args, capture_output=True, text=True, timeout=15, shell=False, **_kw):
        assert capture_output is True
        assert text is True
        assert shell is False
        assert isinstance(args, list)
        calls.append({"args": args, "timeout": timeout})
        if args[:2] == ["gh", "--version"]:
            return FakeProc(stdout="gh version 2.45.0 (2024-04-01)\nhttps://github.com/cli/cli/releases/tag/v2.45.0\n")
        if args[:3] == ["gh", "auth", "status"]:
            return FakeProc(
                stdout="github.com\n  Logged in to github.com as octocat (oauth_token)\n"
            )
        if args[:3] == ["gh", "repo", "view"]:
            return FakeProc(stdout='{"name":"r","visibility":"PUBLIC","isPrivate":false}')
        return FakeProc(stdout="", returncode=0)

    monkeypatch.setattr(shell_mod.shutil, "which", fake_which)
    monkeypatch.setattr(shell_mod.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def gh_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_mod.shutil, "which", lambda _b: None)

    def must_not_call(*_a, **_k):
        raise AssertionError("subprocess.run não deve ser chamado quando gh ausente")

    monkeypatch.setattr(shell_mod.subprocess, "run", must_not_call)


def test_command_result_to_dict_omits_truncated_when_false() -> None:
    r = CommandResult(
        tool="shell",
        cmd_joined="gh --version",
        exit_code=0,
        stdout="ok",
        captured_at="2026-05-10T20:00:00+00:00",
    )
    d = r.to_dict()
    assert d == {
        "tool": "shell",
        "cmd_joined": "gh --version",
        "exit_code": 0,
        "stdout": "ok",
        "captured_at": "2026-05-10T20:00:00+00:00",
    }
    assert "truncated" not in d


def test_command_result_to_dict_includes_truncated_when_true() -> None:
    r = CommandResult(
        tool="shell",
        cmd_joined="gh repo view",
        exit_code=0,
        stdout="x" * STDOUT_MAX_CHARS,
        captured_at="2026-05-10T20:00:00+00:00",
        truncated=True,
        extract="gh_repo_view",
    )
    d = r.to_dict()
    assert d["truncated"] is True
    assert d["extract"] == "gh_repo_view"


def test_collect_shell_evidence_happy_path(gh_present: List[Dict[str, Any]]) -> None:
    cmds = [
        ShellCommand(tool="shell", cmd=["gh", "--version"], extract="gh_version"),
        ShellCommand(tool="shell", cmd=["gh", "auth", "status"], extract="gh_auth"),
    ]
    results = collect_shell_evidence(cmds)
    assert len(results) == 2
    for r in results:
        assert r.tool == "shell"
        assert r.exit_code == 0
        assert r.stdout
        assert ISO_RE.match(r.captured_at), f"captured_at não é ISO8601 UTC: {r.captured_at}"
    assert results[0].cmd_joined == "gh --version"
    assert "2.45.0" in results[0].stdout
    assert results[1].cmd_joined == "gh auth status"
    assert "Logged in" in results[1].stdout
    assert [c["args"][:2] for c in gh_present[:1]] == [["gh", "--version"]]


def test_collect_shell_evidence_subprocess_called_without_shell(
    gh_present: List[Dict[str, Any]],
) -> None:
    collect_shell_evidence([ShellCommand(tool="shell", cmd=["gh", "--version"])])
    assert gh_present[0]["args"] == ["gh", "--version"]
    assert gh_present[0]["timeout"] == 15


def test_collect_shell_evidence_when_gh_missing(gh_absent: None) -> None:
    results = collect_shell_evidence(
        [ShellCommand(tool="shell", cmd=["gh", "--version"])]
    )
    assert len(results) == 1
    r = results[0]
    assert r.exit_code == GH_NOT_FOUND_EXIT_CODE
    assert r.stdout == GH_NOT_FOUND_MESSAGE
    assert r.cmd_joined == "gh --version"
    assert ISO_RE.match(r.captured_at)


def test_collect_shell_evidence_truncates_long_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_mod.shutil, "which", lambda _b: "/usr/local/bin/gh")
    big = "a" * (STDOUT_MAX_CHARS * 2)
    monkeypatch.setattr(
        shell_mod.subprocess,
        "run",
        lambda *a, **k: FakeProc(stdout=big),
    )
    results = collect_shell_evidence(
        [ShellCommand(tool="shell", cmd=["gh", "repo", "view", "u/r"])]
    )
    r = results[0]
    assert len(r.stdout) == STDOUT_MAX_CHARS
    assert r.truncated is True
    assert r.to_dict()["truncated"] is True


def test_collect_shell_evidence_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_mod.shutil, "which", lambda _b: "/usr/local/bin/gh")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 15))

    monkeypatch.setattr(shell_mod.subprocess, "run", boom)
    results = collect_shell_evidence(
        [ShellCommand(tool="shell", cmd=["gh", "auth", "status"])]
    )
    assert results[0].exit_code == GH_NOT_FOUND_EXIT_CODE
    assert "timeout" in results[0].stdout.lower()


def test_commands_for_exercise_12_includes_repo_view_when_url_given() -> None:
    cmds = commands_for_exercise("1.2", "https://github.com/octo/repo.git")
    joined = [" ".join(c.cmd) for c in cmds]
    assert "gh --version" in joined
    assert "gh auth status" in joined
    assert any("gh repo view octo/repo" in j for j in joined)


def test_commands_for_exercise_12_skips_repo_view_when_url_unparseable() -> None:
    cmds = commands_for_exercise("1.2", "not-a-github-url")
    joined = [" ".join(c.cmd) for c in cmds]
    assert "gh --version" in joined
    assert "gh auth status" in joined
    assert not any("gh repo view" in j for j in joined)


def test_commands_for_exercise_12_supports_ssh_url() -> None:
    cmds = commands_for_exercise("1.2", "git@github.com:octo/r.git")
    joined = [" ".join(c.cmd) for c in cmds]
    assert any("gh repo view octo/r" in j for j in joined)


def test_commands_for_exercise_other_id_returns_empty() -> None:
    assert commands_for_exercise("1.1", "https://github.com/o/r") == []
    assert commands_for_exercise("9.9", None) == []


def test_collect_for_exercise_12_payload_shape(
    gh_present: List[Dict[str, Any]],
) -> None:
    results = collect_for_exercise("1.2", "https://github.com/octo/repo")
    payload = [r.to_dict() for r in results]
    assert len(payload) == 3
    expected_keys = {"tool", "cmd_joined", "exit_code", "stdout", "captured_at"}
    for entry in payload:
        assert expected_keys.issubset(entry.keys())
        assert entry["tool"] == "shell"
        assert isinstance(entry["exit_code"], int)
        assert isinstance(entry["stdout"], str)
        assert ISO_RE.match(entry["captured_at"])
    cmd_strings = [entry["cmd_joined"] for entry in payload]
    assert "gh --version" in cmd_strings
    assert "gh auth status" in cmd_strings
    assert any("gh repo view octo/repo" in s for s in cmd_strings)


def test_collect_for_exercise_11_returns_empty_payload(
    gh_present: List[Dict[str, Any]],
) -> None:
    assert collect_for_exercise("1.1", "https://github.com/u/r") == []
    assert gh_present == []


def test_collect_for_exercise_when_gh_missing_keeps_first_two(gh_absent: None) -> None:
    results = collect_for_exercise("1.2", "https://github.com/octo/r")
    assert len(results) == 3
    assert all(r.exit_code == GH_NOT_FOUND_EXIT_CODE for r in results)
    assert all(r.stdout == GH_NOT_FOUND_MESSAGE for r in results)
