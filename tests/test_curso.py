"""Testes do módulo de curso do CLI — paridade com ``app/curso.py`` do backend."""
from __future__ import annotations

import pytest

from autograde_idp.curso import (
    CURSO_DEFAULT,
    CursoError,
    curso_of,
    exercises_base_url,
    qualify_exercise_id,
    split_exercise_id,
)


def test_id_sem_prefixo_e_curso_default() -> None:
    assert split_exercise_id("1.1") == ("td", "1.1")
    assert CURSO_DEFAULT == "td"


def test_id_com_prefixo_separa_curso_e_base() -> None:
    assert split_exercise_id("ia-1.1") == ("ia", "1.1")
    assert curso_of("ia-1.4") == "ia"


@pytest.mark.parametrize("bad", ["", "ia-", "-1.1", "IA-1.1", "ia_1.1", "foo"])
def test_id_malformado_levanta(bad: str) -> None:
    with pytest.raises(CursoError):
        split_exercise_id(bad)


def test_qualify_e_inverso_de_split() -> None:
    assert qualify_exercise_id("td", "1.2") == "1.2"
    assert qualify_exercise_id("ia", "1.2") == "ia-1.2"


def test_default_por_curso_nao_exige_env_var(monkeypatch) -> None:
    # O aluno não configura nada: cada curso já sabe seu repositório.
    monkeypatch.delenv("AUTOGRADE_EXERCISES_BASE_URL", raising=False)
    monkeypatch.delenv("AUTOGRADE_EXERCISES_BASE_URL_IA", raising=False)
    assert exercises_base_url("td").endswith("/idp_governodigital/main/exercicios")
    assert exercises_base_url("ia").endswith("/idp_agentes_ia/main/exercicios")


def test_env_especifica_tem_precedencia_sobre_generica(monkeypatch) -> None:
    monkeypatch.setenv("AUTOGRADE_EXERCISES_BASE_URL", "https://exemplo/geral/")
    monkeypatch.setenv("AUTOGRADE_EXERCISES_BASE_URL_IA", "https://exemplo/ia/")
    assert exercises_base_url("ia") == "https://exemplo/ia"
    assert exercises_base_url("td") == "https://exemplo/geral"


def test_curso_desconhecido_sem_env_levanta(monkeypatch) -> None:
    monkeypatch.delenv("AUTOGRADE_EXERCISES_BASE_URL", raising=False)
    with pytest.raises(CursoError):
        exercises_base_url("zz")


def test_paridade_de_split_com_o_backend() -> None:
    """O backend recusa evidência submetida sob um id que ele parseia
    diferente — os dois lados precisam concordar bit a bit."""
    casos = ["1.1", "1.4", "ia-1.1", "ia-1.4", "4.2", "5.1"]
    esperado = [("td", "1.1"), ("td", "1.4"), ("ia", "1.1"), ("ia", "1.4"),
                ("td", "4.2"), ("td", "5.1")]
    assert [split_exercise_id(c) for c in casos] == esperado
