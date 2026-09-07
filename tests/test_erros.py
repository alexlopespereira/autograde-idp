"""Mensagens de erro acionáveis (regressão do relato real do aluno).

O caso que originou o módulo: `autograde validar ia-1.3` respondia

    /grade-preview falhou: HTTP 403 {"error":"turma_not_eligible"}

e o aluno, sem mais nada pra seguir, tentava `autograde login` — que não tem
relação com o problema. Os testes abaixo travam as três garantias que evitam
isso: título em português, passos concretos, e link de FAQ.
"""

from __future__ import annotations

import io

import pytest

from autograde_idp import erros


def test_parse_error_body_extrai_codigo_e_message():
    codigo, msg = erros.parse_error_body('{"error":"turma_not_eligible","message":"oi"}')
    assert codigo == "turma_not_eligible"
    assert msg == "oi"


def test_parse_error_body_tolera_corpo_nao_json():
    codigo, msg = erros.parse_error_body("<html>502 Bad Gateway</html>")
    assert codigo == ""
    assert "502" in msg


def test_parse_error_body_tolera_corpo_vazio():
    assert erros.parse_error_body("") == ("", "")


def test_turma_not_eligible_nao_manda_o_aluno_fazer_login():
    out = erros.explicar_http(403, '{"error":"turma_not_eligible"}')
    assert "turma" in out.lower()
    # O passo tem que dizer explicitamente que login NÃO resolve — senão o
    # aluno repete o erro do relato original.
    assert "não resolve" in out
    assert "autograde whoami" in out
    assert "FAQ.md#turma_not_eligible" in out
    assert "403" in out


def test_message_do_backend_aparece_junto_dos_passos():
    """Backend traz os valores concretos; o registry traz o roteiro."""
    out = erros.explicar_http(
        403,
        '{"error":"turma_not_eligible","message":"Voce esta em TD-2026-01."}',
    )
    assert "TD-2026-01" in out           # veio do backend
    assert "autograde whoami" in out     # veio do registry local


def test_backend_antigo_sem_message_ainda_produz_instrucoes():
    """CLI nova contra backend velho: o registry sozinho tem que bastar."""
    out = erros.explicar_http(403, '{"error":"not_in_roster"}')
    assert "O que fazer:" in out
    assert "institucional" in out


def test_codigo_desconhecido_nao_quebra():
    out = erros.explicar_http(418, '{"error":"chaleira"}')
    assert "418" in out
    assert "chaleira" in out


def test_5xx_sem_codigo_diz_que_nao_e_culpa_do_aluno():
    out = erros.explicar_http(500, "")
    assert "problema do servidor" in out


def test_erro_de_rede_orienta_conexao():
    out = erros.explicar_rede(OSError("connection refused"))
    assert "connection refused" in out
    assert "internet" in out


def test_explicar_sem_repo_ensina_o_cd():
    out = erros.explicar_sem_repo("ia-1.3")
    assert "git config --get remote.origin.url" in out
    assert "autograde validar ia-1.3" in out
    assert "gh repo clone" in out


@pytest.mark.parametrize(
    "codigo",
    ["rate_limit_preview_cooldown", "rate_limit_preview_daily_cap", "resposta_empty"],
)
def test_aliases_reusam_a_explicacao_canonica(codigo):
    out = erros.explicar_http(429, '{"error":"%s"}' % codigo)
    assert "O que fazer:" in out


class _StdoutCp1252(io.StringIO):
    encoding = "cp1252"


class _StdoutUtf8(io.StringIO):
    encoding = "utf-8"


def test_simbolos_degradam_para_ascii_no_console_do_windows(monkeypatch):
    """Console legado do Windows levanta UnicodeEncodeError em '✗' e '⚠'."""
    monkeypatch.setattr("sys.stdout", _StdoutCp1252())
    out = erros.explicar_http(403, '{"error":"turma_not_eligible"}')
    assert "[X]" in out
    out.encode("cp1252")  # não pode levantar

    monkeypatch.setattr("sys.stdout", _StdoutUtf8())
    assert "✗" in erros.explicar_http(403, '{"error":"turma_not_eligible"}')
