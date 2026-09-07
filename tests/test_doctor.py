"""`autograde doctor` — diagnóstico completo numa passada.

O ponto do comando é NÃO parar no primeiro problema: o aluno que não leu o
tutorial precisa ver a lista inteira do que falta, não descobrir um item por
execução. Os testes travam isso e o formato de saída (todo item que falha traz
o comando que conserta).
"""

from __future__ import annotations

import io

import pytest

from autograde_idp import doctor
from autograde_idp.doctor import FAIL, OK, WARN, Check


def _fake_run(mapa: dict[str, tuple[int, str]]):
    def run(cmd: list[str]) -> tuple[int, str]:
        return mapa.get(" ".join(cmd), (127, f"{cmd[0]} não encontrado no PATH"))

    return run


def test_check_git_ausente_traz_instrucao_de_instalacao(monkeypatch):
    monkeypatch.setattr(doctor, "_run", _fake_run({}))
    c = doctor.check_git()
    assert c.status == FAIL
    assert any("git-scm.com" in p for p in c.conserto)
    assert any("terminal NOVO" in p for p in c.conserto)


def test_check_git_identity_falha_sem_user_email(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run",
        _fake_run({"git config --global user.name": (0, "Ana")}),
    )
    c = doctor.check_git_identity()
    assert c.status == FAIL
    assert any("user.email" in p for p in c.conserto)


def test_check_gh_ausente_avisa_dos_pontos_perdidos(monkeypatch):
    monkeypatch.setattr(doctor, "_run", _fake_run({}))
    c = doctor.check_gh()
    assert c.status == FAIL
    assert any("40 pontos" in p for p in c.conserto)


def test_check_gh_auth_deslogado_manda_rodar_gh_auth_login(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run",
        _fake_run({"gh auth status": (1, "You are not logged into any GitHub hosts")}),
    )
    c = doctor.check_gh_auth()
    assert c.status == FAIL
    assert "gh auth login" in c.conserto


def test_check_roster_usa_turmas_plural(monkeypatch):
    checks = doctor.check_roster(
        {
            "email": "ana@idp.edu.br",
            "turmas": ["TD-2026-01", "IA-2026-02"],
            "github_username": "ana",
        }
    )
    turma_check = next(c for c in checks if c.nome == "turma(s)")
    assert turma_check.status == OK
    assert "TD-2026-01, IA-2026-02" == turma_check.detalhe


def test_check_roster_cai_para_turma_singular_em_backend_antigo():
    checks = doctor.check_roster(
        {"email": "ana@idp.edu.br", "turma": "TD-2026-01", "github_username": "ana"}
    )
    turma_check = next(c for c in checks if c.nome == "turma(s)")
    assert turma_check.detalhe == "TD-2026-01"


def test_check_roster_sinaliza_github_username_vazio():
    checks = doctor.check_roster(
        {"email": "ana@idp.edu.br", "turma": "TD-2026-01", "github_username": ""}
    )
    gh = next(c for c in checks if c.nome == "github_username no roster")
    assert gh.status == FAIL


def test_check_repo_fora_de_repo_e_aviso_nao_erro(monkeypatch):
    """Rodar `doctor` no home do usuário é legítimo — não pode virar FAIL."""
    monkeypatch.setattr(doctor, "_run", _fake_run({}))
    checks = doctor.check_repo(None, None)
    assert [c.status for c in checks] == [WARN]


def test_check_repo_detecta_dono_diferente(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run",
        _fake_run(
            {
                "git config --get remote.origin.url": (
                    0,
                    "https://github.com/outra-pessoa/repo.git",
                ),
                "gh repo view outra-pessoa/repo --json visibility": (
                    0,
                    '{"visibility":"PUBLIC"}',
                ),
            }
        ),
    )
    checks = doctor.check_repo(None, {"github_username": "ana"})
    dono = next(c for c in checks if c.nome == "repo pertence a você")
    assert dono.status == FAIL


def test_check_repo_detecta_repo_privado(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run",
        _fake_run(
            {
                "git config --get remote.origin.url": (
                    0,
                    "https://github.com/ana/repo.git",
                ),
                "gh repo view ana/repo --json visibility": (
                    0,
                    '{"visibility":"PRIVATE"}',
                ),
            }
        ),
    )
    checks = doctor.check_repo(None, {"github_username": "ana"})
    publico = next(c for c in checks if c.nome == "repo público")
    assert publico.status == FAIL
    assert any("Change visibility" in p for p in publico.conserto)


def test_render_lista_todos_os_problemas_de_uma_vez():
    checks = [
        Check("git instalado", FAIL, "não achei", ("instale o git",)),
        Check("gh instalado", FAIL, "não achei", ("instale o gh",)),
        Check("Python 3.9+", OK, "3.11.2"),
    ]
    out = doctor.render(checks)
    assert "instale o git" in out
    assert "instale o gh" in out
    assert "2 item(ns)" in out


def test_render_sem_problemas_convida_a_validar():
    out = doctor.render([Check("Python 3.9+", OK, "3.11.2")])
    assert "Tudo pronto" in out


class _StdoutCp1252(io.StringIO):
    encoding = "cp1252"


def test_render_nao_quebra_no_console_do_windows(monkeypatch):
    monkeypatch.setattr("sys.stdout", _StdoutCp1252())
    out = doctor.render([Check("git instalado", FAIL, "x", ("instale",))])
    out.encode("cp1252")  # não pode levantar
    assert "[X]" in out


@pytest.mark.parametrize("status,esperado", [(FAIL, 1), (OK, 0), (WARN, 0)])
def test_exit_code_reflete_so_falhas(monkeypatch, status, esperado):
    monkeypatch.setattr(doctor, "coletar", lambda cwd=None: [Check("x", status)])
    assert doctor.run_doctor(print_fn=lambda _s: None) == esperado
