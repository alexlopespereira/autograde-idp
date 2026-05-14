"""Testes do comando autograde validar (US-09).

Cobre: detecção de repo git, in-flight lock, geração/persistência de uuid,
fluxo happy path, exercício ambíguo (sem marcador), conflito de lock,
retry com mesmo uuid após falha de rede no /submissions.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

from autograde_idp import validar
from autograde_idp.auth import TokenBundle
from autograde_idp.evidence.shell import CommandResult

GIT_BIN = "git"


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([GIT_BIN, "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([GIT_BIN, "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run([GIT_BIN, "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run([GIT_BIN, "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(
        [GIT_BIN, "remote", "add", "origin", "https://github.com/u/r.git"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture(autouse=True)
def _isolate_repo_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Isola repo-map.json por test. Sem isso, remember_repo escreve no
    ~/.git-exercicios/ real e contamina outros tests com estado persistido."""
    isolated = tmp_path_factory.mktemp("repo-map-iso") / "repo-map.json"
    monkeypatch.setattr(validar, "repo_map_path", lambda *_a, **_k: isolated)


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> TokenBundle:
    bundle = TokenBundle(
        access_token="at-test",
        refresh_token="rt-test",
        id_token="h.e.s",
        expires_at=1e12,
        first_login_at=1e9,
        client_id="cid",
    )
    monkeypatch.setattr(validar, "load_token", lambda: bundle)
    monkeypatch.setattr(validar, "ensure_fresh_token", lambda b, *_a, **_k: b)
    return bundle


class FakeResp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


def test_detect_repo_url_returns_origin(git_repo: Path) -> None:
    assert validar.detect_repo_url(git_repo) == "https://github.com/u/r.git"


def test_detect_repo_url_raises_outside_repo(tmp_path: Path) -> None:
    with pytest.raises(validar.ValidarError, match="remote origin"):
        validar.detect_repo_url(tmp_path)


def test_discover_exercise_id_reads_marker(tmp_path: Path) -> None:
    (tmp_path / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    assert validar.discover_exercise_id(tmp_path) == "1.1"


def test_discover_exercise_id_raises_when_ambiguous(tmp_path: Path) -> None:
    with pytest.raises(validar.ValidarError, match="id"):
        validar.discover_exercise_id(tmp_path)


def test_get_or_create_uuid_persists_same_value(tmp_path: Path) -> None:
    path = tmp_path / "in-flight.json"
    u1 = validar.get_or_create_uuid(path, "1.1")
    u2 = validar.get_or_create_uuid(path, "1.1")
    assert u1 == u2
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["1.1"] == u1


def test_clear_uuid_removes_only_target(tmp_path: Path) -> None:
    path = tmp_path / "in-flight.json"
    u_one = validar.get_or_create_uuid(path, "1.1")
    u_two = validar.get_or_create_uuid(path, "1.2")
    validar.clear_uuid(path, "1.1")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "1.1" not in saved
    assert saved["1.2"] == u_two
    assert u_one != u_two


def test_render_bulletin_shows_marks_and_total() -> None:
    out = validar.render_bulletin(
        {
            "criterios": [
                {"passed": True, "points_earned": 10, "points_max": 10, "message": "alpha"},
                {"passed": False, "points_earned": 0, "points_max": 5, "message": "beta"},
            ],
            "total": 10,
            "max_total": 15,
        }
    )
    assert "10/10" in out and "0/5" in out
    assert "alpha" in out and "beta" in out
    assert "10/15" in out


def test_render_bulletin_wraps_long_message_to_next_line() -> None:
    # Feedback do Gemini pode ser longo (>50 chars). Deve quebrar pra linha
    # separada indentada em vez de fazer scroll horizontal.
    feedback_longo = (
        "Aluno citou git init e git commit corretamente mas não explicou git push, "
        "pelo qual foram descontados 2 pontos do total."
    )
    out = validar.render_bulletin(
        {
            "criterios": [
                {"passed": True, "points_earned": 8, "points_max": 10, "message": feedback_longo},
            ],
            "total": 8,
            "max_total": 10,
        }
    )
    lines = out.splitlines()
    # Linha de header do critério (não confundir com "Total: 8/10")
    crit_lines = [line for line in lines if line.startswith("  ✅ 8/10")]
    assert len(crit_lines) == 1
    assert feedback_longo not in crit_lines[0]
    # Feedback aparece em linha(s) indentada(s) abaixo
    indented = [line for line in lines if line.startswith("      ")]
    assert indented, "feedback deveria estar em linha indentada"
    joined = " ".join(line.strip() for line in indented)
    assert "git init" in joined
    assert "push" in joined


def test_render_bulletin_omits_id_column_even_if_payload_has_id() -> None:
    # CriterioResult do backend não emite id (app/grader.py). Mesmo se vier por
    # acidente, não deve aparecer no output — regressão pro caso "?" no fallback.
    out = validar.render_bulletin(
        {
            "criterios": [
                {
                    "id": "ignored",
                    "passed": True,
                    "points_earned": 1,
                    "points_max": 1,
                    "message": "m",
                },
            ],
            "total": 1,
            "max_total": 1,
        }
    )
    assert "ignored" not in out
    assert "?" not in out


@pytest.mark.skipif(sys.platform == "win32", reason="usa semântica fcntl.flock")
def test_in_flight_lock_conflict_raises(tmp_path: Path) -> None:
    import fcntl

    path = tmp_path / "in-flight.json"
    path.write_text("{}", encoding="utf-8")
    holder = open(path, "r+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(validar.InFlightLockedError):
            with validar.in_flight_locked(path):
                pass
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_run_validar_happy_path_auto_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    calls: list[tuple[str, dict]] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        assert headers == {"Authorization": "Bearer h.e.s"}
        calls.append((url, json or {}))
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {
                        "criterios": [
                            {
                                "id": "repo_publico",
                                "passed": True,
                                "points_earned": 10,
                                "points_max": 10,
                                "message": "ok",
                            }
                        ],
                        "total": 10,
                        "max_total": 100,
                    },
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        if url.endswith("/submissions"):
            assert json["submission_uuid"]
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 10, "max_total": 100},
                    "submission_id": json["submission_uuid"],
                    "written": True,
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id=None,
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "10/10" in out  # points do critério renderizado
    assert "Total: 10/100" in out
    assert "Submetido" in out
    grade_url, grade_body = calls[0]
    submit_url, submit_body = calls[1]
    assert grade_url == "http://test.local/grade-preview"
    assert submit_url == "http://test.local/submissions"
    assert grade_body["exercicio"] == "1.1"
    assert grade_body["repo_url"] == "https://github.com/u/r.git"
    assert submit_body["submission_uuid"]
    assert json.loads(in_flight.read_text(encoding="utf-8")) == {}


def test_run_validar_sends_shell_evidence_for_ex_1_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC5: exercise 1.2 dispara collect_for_exercise e envia shell_evidence
    em /grade-preview e /submissions."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.2\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    fake_results = [
        CommandResult(
            tool="shell",
            cmd_joined="gh --version",
            exit_code=0,
            stdout="gh version 2.40.0 (2024-01-15)",
            captured_at="2026-05-10T22:00:00+00:00",
            extract="gh_version",
        ),
        CommandResult(
            tool="shell",
            cmd_joined="gh auth status",
            exit_code=0,
            stdout="Logged in to github.com as student",
            captured_at="2026-05-10T22:00:01+00:00",
            extract="gh_auth",
        ),
    ]

    captured_args: dict[str, Any] = {}

    def fake_collect(exercise_id, repo_url, *_a, **_k):
        captured_args["exercise_id"] = exercise_id
        captured_args["repo_url"] = repo_url
        return fake_results

    monkeypatch.setattr(validar, "collect_for_exercise", fake_collect)

    calls: list[tuple[str, dict]] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json or {}))
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        return FakeResp(
            200,
            {
                "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                "submission_id": json["submission_uuid"],
                "written": True,
                "late": False,
                "dias_apos_recomendado": 0,
            },
        )

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id=None,
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc == 0
    assert captured_args["exercise_id"] == "1.2"
    assert captured_args["repo_url"] == "https://github.com/u/r.git"

    grade_url, grade_body = calls[0]
    submit_url, submit_body = calls[1]
    assert grade_url == "http://test.local/grade-preview"
    assert submit_url == "http://test.local/submissions"

    expected_evidence = [r.to_dict() for r in fake_results]
    assert grade_body["shell_evidence"] == expected_evidence
    assert submit_body["shell_evidence"] == expected_evidence
    assert grade_body["exercicio"] == "1.2"
    assert submit_body["exercicio"] == "1.2"
    assert submit_body["submission_uuid"]


def test_run_validar_exits_2_when_exercise_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"

    def must_not_post(*_a, **_k):
        raise AssertionError("HTTP não deveria ser chamado")

    monkeypatch.setattr(validar.requests, "post", must_not_post)

    rc = validar.run_validar(
        exercise_id=None,
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "exerc" in err or "id" in err


def test_run_validar_exits_2_when_not_a_git_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"
    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=tmp_path,
        in_flight=in_flight,
    )
    assert rc == 2
    assert "remote origin" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="usa semântica fcntl.flock")
def test_run_validar_in_flight_lock_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import fcntl

    in_flight = tmp_path / "in-flight.json"
    in_flight.write_text("{}", encoding="utf-8")
    holder = open(in_flight, "r+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def must_not_post(*_a, **_k):
        raise AssertionError("HTTP não deveria ser chamado quando lock está preso")

    monkeypatch.setattr(validar.requests, "post", must_not_post)
    try:
        rc = validar.run_validar(
            exercise_id="1.1",
            auto_submit=True,
            cwd=git_repo,
            in_flight=in_flight,
        )
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    assert rc == 2
    assert "rodando" in capsys.readouterr().err.lower()


def test_run_validar_retry_reuses_uuid_after_network_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")
    state = {"fail_submit": True, "submit_uuid": None, "submit_calls": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 100, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        if url.endswith("/submissions"):
            state["submit_calls"] += 1
            state["submit_uuid"] = json["submission_uuid"]
            if state["fail_submit"]:
                raise requests.ConnectionError("network down")
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 100, "max_total": 100},
                    "submission_id": json["submission_uuid"],
                    "written": True,
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc1 = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc1 == 3
    first_uuid = state["submit_uuid"]
    assert first_uuid is not None
    saved = json.loads(in_flight.read_text(encoding="utf-8"))
    assert saved["1.1"] == first_uuid

    state["fail_submit"] = False
    rc2 = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc2 == 0
    assert state["submit_uuid"] == first_uuid
    assert state["submit_calls"] == 2
    assert json.loads(in_flight.read_text(encoding="utf-8")) == {}


def test_run_validar_prompt_no_skips_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")
    submit_called = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        submit_called["n"] += 1
        raise AssertionError("submissions não deveria ser chamado")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=False,
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _prompt: "n",
    )
    assert rc == 0
    assert submit_called["n"] == 0
    assert "cancelada" in capsys.readouterr().out.lower()
    saved = json.loads(in_flight.read_text(encoding="utf-8"))
    assert saved["1.1"]


def test_run_validar_4xx_final_clears_uuid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    in_flight = tmp_path / "in-flight.json"
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        return FakeResp(403, {"error": "repo_owner_mismatch"})

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc == 3
    assert json.loads(in_flight.read_text(encoding="utf-8")) == {}


# ---------- collect_respostas (perguntas subjetivas) ----------


def test_collect_respostas_returns_empty_when_no_perguntas() -> None:
    result = validar.collect_respostas([])
    assert result == []


def test_collect_respostas_collects_answers_in_order() -> None:
    inputs = iter(["resposta um", "resposta dois"])
    outputs: list[str] = []
    result = validar.collect_respostas(
        [{"texto": "Q1?", "peso": 10}, {"texto": "Q2?", "peso": 5}],
        input_fn=lambda _prompt: next(inputs),
        print_fn=outputs.append,
    )
    assert result == ["resposta um", "resposta dois"]
    # ambas as perguntas aparecem no output
    joined = "\n".join(outputs)
    assert "Q1?" in joined
    assert "Q2?" in joined


def test_collect_respostas_loops_until_non_empty() -> None:
    # vazio, espaço, válido → deve retornar só o último
    inputs = iter(["", "   ", "finalmente"])
    outputs: list[str] = []
    result = validar.collect_respostas(
        [{"texto": "Q?", "peso": 10}],
        input_fn=lambda _prompt: next(inputs),
        print_fn=outputs.append,
    )
    assert result == ["finalmente"]
    # mensagem de aviso aparece 2x (após cada vazio)
    aviso = [line for line in outputs if "vazia" in line.lower()]
    assert len(aviso) == 2


def test_collect_respostas_strips_whitespace() -> None:
    result = validar.collect_respostas(
        [{"texto": "Q", "peso": 5}],
        input_fn=lambda _p: "   com espaços ao redor   ",
        print_fn=lambda _s: None,
    )
    assert result == ["com espaços ao redor"]


def test_collect_respostas_raises_eoferror_on_eof() -> None:
    def boom(_prompt: str) -> str:
        raise EOFError()

    with pytest.raises(EOFError):
        validar.collect_respostas(
            [{"texto": "Q", "peso": 5}], input_fn=boom, print_fn=lambda _s: None
        )


def test_run_validar_prompts_perguntas_and_sends_respostas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
) -> None:
    """E2E: backend retorna perguntas → CLI pergunta → submission inclui respostas."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    submit_body_seen: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                    "perguntas": [
                        {"texto": "O que você entendeu?", "peso": 10},
                    ],
                },
            )
        if url.endswith("/submissions"):
            submit_body_seen.update(json or {})
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 7, "max_total": 110},
                    "submission_id": json["submission_uuid"],
                    "written": True,
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    # input_fn é chamado pra (a) pergunta e (b) prompt s/n quando auto_submit=False
    inputs = iter(["entendi git init e commit", "s"])
    rc = validar.run_validar(
        exercise_id=None,
        auto_submit=False,
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _prompt: next(inputs),
    )
    assert rc == 0
    assert submit_body_seen.get("respostas") == ["entendi git init e commit"]


def test_run_validar_calls_grade_preview_twice_when_perguntas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
) -> None:
    """Quando exercício tem perguntas: 1ª /grade-preview pra descobrir, 2ª com respostas."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    grade_preview_calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            grade_preview_calls.append(json or {})
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                    "perguntas": [{"texto": "Q?", "peso": 10}],
                },
            )
        if url.endswith("/submissions"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 5, "max_total": 110},
                    "submission_id": json["submission_uuid"],
                    "written": True,
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,  # auto: pula s/n, mas NÃO pula perguntas
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _p: "resposta valida",
    )
    assert rc == 0
    assert len(grade_preview_calls) == 2
    # primeira sem respostas, segunda com
    assert "respostas" not in grade_preview_calls[0]
    assert grade_preview_calls[1]["respostas"] == ["resposta valida"]


def test_run_validar_calls_grade_preview_once_when_no_perguntas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
) -> None:
    """Backward compat: sem perguntas = uma única chamada (comportamento antigo)."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    preview_count = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            preview_count["n"] += 1
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 10, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                },  # sem perguntas
            )
        if url.endswith("/submissions"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 10, "max_total": 100},
                    "submission_id": json["submission_uuid"],
                    "written": True,
                    "late": False,
                    "dias_apos_recomendado": 0,
                },
            )
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
    )
    assert rc == 0
    assert preview_count["n"] == 1


def test_detect_repo_mismatch_returns_other_exercise_id(tmp_path: Path) -> None:
    map_path = tmp_path / "repo-map.json"
    validar.remember_repo("1.3", "https://github.com/u/meu-segundo-rep.git", map_path)
    # Repo já usado pra 1.3 → rodar 1.4 com mesmo repo deve apontar conflito
    conflict = validar.detect_repo_mismatch(
        "1.4", "https://github.com/u/meu-segundo-rep.git", map_path
    )
    assert conflict == "1.3"


def test_detect_repo_mismatch_silent_for_same_exercise(tmp_path: Path) -> None:
    map_path = tmp_path / "repo-map.json"
    validar.remember_repo("1.1", "https://github.com/u/r.git", map_path)
    # Re-submissão do mesmo exercício no mesmo repo NÃO deve avisar
    assert validar.detect_repo_mismatch("1.1", "https://github.com/u/r.git", map_path) is None


def test_detect_repo_mismatch_silent_for_brand_new_repo(tmp_path: Path) -> None:
    map_path = tmp_path / "repo-map.json"
    validar.remember_repo("1.1", "https://github.com/u/a.git", map_path)
    # Repo nunca visto → sem aviso (primeira tentativa pra exercise novo)
    assert validar.detect_repo_mismatch("1.2", "https://github.com/u/novo.git", map_path) is None


def test_remember_repo_dedupes(tmp_path: Path) -> None:
    map_path = tmp_path / "repo-map.json"
    validar.remember_repo("1.1", "https://github.com/u/r.git", map_path)
    validar.remember_repo("1.1", "https://github.com/u/r.git", map_path)
    data = json.loads(map_path.read_text(encoding="utf-8"))
    assert data["1.1"] == ["https://github.com/u/r.git"]


def test_run_validar_warns_and_prompts_on_repo_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Repo do git_repo já marcado pra 1.3 → validar 1.4 dispara warning."""
    in_flight = tmp_path / "in-flight.json"
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    # Marca o repo do fixture como usado pra 1.3
    repo_map_file = validar.repo_map_path()
    validar.remember_repo("1.3", "https://github.com/u/r.git", repo_map_file)

    # input_fn responde "n" no prompt de confirmação → aborta
    rc = validar.run_validar(
        exercise_id="1.4",
        auto_submit=False,
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _p: "n",
    )
    out = capsys.readouterr().out
    assert rc == 2  # cancelado por escolha do user
    assert "1.3" in out  # warning cita o exercício do conflito
    assert "Cancelado" in out


def test_run_validar_preview_429_preserves_uuid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
) -> None:
    """Rate-limit no segundo /grade-preview (com respostas) preserva UUID pra retry."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    call_count = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeResp(
                    200,
                    {
                        "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                        "late": False,
                        "dias_apos_recomendado": 0,
                        "perguntas": [{"texto": "Q?", "peso": 10}],
                    },
                )
            # segunda call (com respostas) → 429
            return FakeResp(429, {"error": "rate_limit_preview_daily_cap"})
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=True,
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _p: "minha resposta",
    )
    assert rc == 3
    # UUID preservado — aluno pode esperar reset à meia-noite e tentar de novo
    persisted = json.loads(in_flight.read_text(encoding="utf-8"))
    assert "1.1" in persisted


def test_run_validar_429_preserves_uuid_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_repo: Path,
    fake_token: TokenBundle,
) -> None:
    """Rate-limit do backend (429) deve preservar UUID — aluno tenta de novo após cooldown."""
    in_flight = tmp_path / "in-flight.json"
    (git_repo / ".autograde-exercise").write_text("1.1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOGRADE_API_URL", "http://test.local")

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/grade-preview"):
            return FakeResp(
                200,
                {
                    "bulletin": {"criterios": [], "total": 0, "max_total": 100},
                    "late": False,
                    "dias_apos_recomendado": 0,
                    "perguntas": [{"texto": "Q?", "peso": 10}],
                },
            )
        if url.endswith("/submissions"):
            return FakeResp(429, {"error": "rate_limit_cooldown"})
        raise AssertionError(f"url inesperada: {url}")

    monkeypatch.setattr(validar.requests, "post", fake_post)

    inputs = iter(["minha resposta", "s"])
    rc = validar.run_validar(
        exercise_id="1.1",
        auto_submit=False,
        cwd=git_repo,
        in_flight=in_flight,
        input_fn=lambda _p: next(inputs),
    )
    assert rc == 3
    # UUID preservado (não foi limpo) — diferente do 4xx que limpa
    persisted = json.loads(in_flight.read_text(encoding="utf-8"))
    assert "1.1" in persisted
