"""Toda âncora citada numa mensagem de erro precisa existir na FAQ.

Um link de FAQ que cai no topo da página é pior do que link nenhum: o aluno
que já está travado gasta mais tempo procurando. Este teste é barato e pega o
caso mais provável de apodrecimento — alguém renomeia uma seção da FAQ e
esquece do `erros.py` (ou vice-versa).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autograde_idp import erros

FAQ = Path(__file__).resolve().parent.parent / "docs" / "FAQ.md"

# Âncoras usadas fora do REGISTRY (mensagens montadas à mão).
ANCORAS_EXTRAS = {"nao_estou_num_repo", "erro_de_rede", "erro_5xx", "perfil"}


def _ancoras_da_faq() -> set[str]:
    texto = FAQ.read_text(encoding="utf-8")
    return set(re.findall(r'<a id="([^"]+)"></a>', texto))


def test_faq_existe():
    assert FAQ.is_file(), f"FAQ não encontrada em {FAQ}"


@pytest.mark.parametrize(
    "codigo", sorted(c for c, e in erros.REGISTRY.items() if e.anchor)
)
def test_ancora_do_registry_existe_na_faq(codigo):
    anchor = erros.REGISTRY[codigo].anchor
    assert anchor in _ancoras_da_faq(), (
        f"erros.REGISTRY[{codigo!r}] aponta para #{anchor}, "
        f"que não existe em docs/FAQ.md"
    )


@pytest.mark.parametrize("anchor", sorted(ANCORAS_EXTRAS))
def test_ancora_avulsa_existe_na_faq(anchor):
    assert anchor in _ancoras_da_faq()


def test_faq_url_aponta_para_o_arquivo_deste_repo():
    assert erros.FAQ_URL.endswith("/docs/FAQ.md")


def test_faq_cobre_os_comandos_que_as_mensagens_mandam_rodar():
    """Se um erro manda rodar `autograde doctor`, a FAQ tem que explicá-lo."""
    texto = FAQ.read_text(encoding="utf-8")
    for comando in ("autograde doctor", "autograde perfil", "autograde whoami"):
        assert comando in texto
