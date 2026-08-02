"""Curso derivado do prefixo do id do exercício — espelho de ``app/curso.py``
no backend (autograde-idp-backend).

O autograder serve mais de um curso (Transformação Digital e Agentes de IA)
com numeração de exercícios sobreposta (``1.1`` existe nos dois). O id carrega
o curso como prefixo — ``ia-1.1`` — o que torna o id globalmente único e diz
de qual repositório de YAMLs baixar a spec.

Compatibilidade: id SEM prefixo é o curso legado ``td``. Nada do que o aluno
de TD já rodou muda.

**Mantém paridade com o backend**: `split_exercise_id` e `qualify_exercise_id`
precisam produzir exatamente o mesmo resultado dos dois lados, senão o CLI
manda evidência sob um id que o backend não reconhece.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Tuple

CURSO_DEFAULT = "td"

# Prefixo: 2-8 letras minúsculas + hífen. Id base começa com dígito, o que
# torna o split não-ambíguo.
_EXERCISE_ID_RE = re.compile(r"^(?:(?P<curso>[a-z]{2,8})-)?(?P<base>\d[\w.]*)$")

# Default por curso: o aluno não precisa configurar env var nenhuma.
DEFAULT_BASE_URLS: Dict[str, str] = {
    "td": "https://raw.githubusercontent.com/alexlopespereira/idp_governodigital/main/exercicios",
    "ia": "https://raw.githubusercontent.com/alexlopespereira/idp_agentes_ia/main/exercicios",
}


class CursoError(Exception):
    """Id de exercício malformado ou curso sem base URL conhecida."""


def split_exercise_id(exercise_id: str) -> Tuple[str, str]:
    """``"ia-1.1"`` → ``("ia", "1.1")``; ``"1.1"`` → ``("td", "1.1")``."""
    match = _EXERCISE_ID_RE.match((exercise_id or "").strip())
    if match is None:
        raise CursoError(
            f"id de exercício inválido: {exercise_id!r} "
            "(esperado `1.1` ou `ia-1.1`)"
        )
    return (match.group("curso") or CURSO_DEFAULT), match.group("base")


def curso_of(exercise_id: str) -> str:
    return split_exercise_id(exercise_id)[0]


def qualify_exercise_id(curso: str, base_id: str) -> str:
    """Inverso de :func:`split_exercise_id` — curso default sai sem prefixo."""
    return base_id if curso == CURSO_DEFAULT else f"{curso}-{base_id}"


def exercises_base_url(curso: str) -> str:
    """Base URL dos YAMLs do ``curso``.

    Precedência: ``AUTOGRADE_EXERCISES_BASE_URL_<CURSO>`` → o genérico
    ``AUTOGRADE_EXERCISES_BASE_URL`` (escape hatch pra apontar tudo pra um
    fork/branch de teste) → o default embutido do curso.
    """
    specific = os.environ.get(f"AUTOGRADE_EXERCISES_BASE_URL_{curso.upper()}")
    if specific:
        return specific.rstrip("/")
    generic = os.environ.get("AUTOGRADE_EXERCISES_BASE_URL")
    if generic:
        return generic.rstrip("/")
    default = DEFAULT_BASE_URLS.get(curso)
    if default:
        return default.rstrip("/")
    raise CursoError(
        f"curso {curso!r} sem base URL conhecida — "
        f"defina AUTOGRADE_EXERCISES_BASE_URL_{curso.upper()}"
    )
