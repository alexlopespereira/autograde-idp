"""Fetch + parse do YAML do exercício a partir do raw GitHub do idp_governodigital.

YAML é a fonte única de verdade — backend o consome para validar/grader, CLI
o consome para descobrir quais artefatos coletar e quais comandos shell rodar.
Mantém o CLI alinhado com o backend automaticamente: qualquer mudança no
exercício (nova entrega, novo comando) acontece sem release coordenado dos
dois lados.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict

import requests
import yaml

DEFAULT_EXERCISES_BASE_URL = (
    "https://raw.githubusercontent.com/alexlopespereira/idp_governodigital/main/exercicios"
)


class ExercicioSpecError(Exception):
    """YAML do exercício não encontrado, malformado, ou rede caiu."""


def exercises_base_url() -> str:
    return os.environ.get(
        "AUTOGRADE_EXERCISES_BASE_URL", DEFAULT_EXERCISES_BASE_URL
    ).rstrip("/")


def _http_fetcher(url: str) -> str:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.text


def fetch_exercise_spec(
    exercise_id: str,
    *,
    fetcher: Callable[[str], str] = _http_fetcher,
) -> Dict[str, Any]:
    """Baixa o YAML do exercício e devolve dict parseado.

    Levanta ``ExercicioSpecError`` para qualquer falha (rede, 404, YAML
    malformado) para o CLI mostrar mensagem clara ao aluno.
    """
    url = f"{exercises_base_url()}/{exercise_id}.yaml"
    try:
        text = fetcher(url)
    except requests.RequestException as exc:
        raise ExercicioSpecError(
            f"falha ao buscar especificação do exercício em {url}: {exc}"
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ExercicioSpecError(f"YAML malformado em {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ExercicioSpecError(
            f"YAML em {url} não é mapping no topo (recebi {type(data).__name__})"
        )
    return data
