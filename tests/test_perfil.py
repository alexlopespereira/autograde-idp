"""`autograde perfil` — ver e completar o cadastro fora da janela do login.

Antes, o github_username só era pedido durante o primeiro `autograde login`,
e só se a célula do roster estivesse vazia. Quem pulasse essa janela (login
sem TTY) ou errasse o username não tinha comando nenhum para inspecionar ou
corrigir: descobria o problema como um `repo_owner_mismatch` na hora de
validar. Estes testes travam o novo caminho.
"""

from __future__ import annotations

import pytest

from autograde_idp import profile


@pytest.fixture
def token(monkeypatch):
    class Bundle:
        id_token = "tok"

    monkeypatch.setattr(profile, "fetch_me_identity", lambda api, tok: IDENTITY)
    import autograde_idp.auth as auth_mod

    monkeypatch.setattr(auth_mod, "load_token", lambda: Bundle())
    monkeypatch.setattr(auth_mod, "ensure_fresh_token", lambda b, api: b)
    monkeypatch.setattr(
        auth_mod, "decode_id_token_unverified", lambda t: {"name": "Ana Silva"}
    )
    return Bundle()


IDENTITY: dict = {}


def _set_identity(**kw):
    IDENTITY.clear()
    IDENTITY.update(
        {
            "email": "ana@aluno.idp.edu.br",
            "nome": "Ana Silva",
            "turmas": ["TD-2026-01", "IA-2026-02"],
            "github_username": "",
            **kw,
        }
    )


def test_perfil_mostra_todas_as_turmas(token, monkeypatch):
    _set_identity(github_username="anasilva")
    out: list[str] = []
    rc = profile.run_perfil(print_fn=out.append)
    assert rc == 0
    texto = "\n".join(out)
    assert "TD-2026-01, IA-2026-02" in texto
    assert "anasilva" in texto


def test_perfil_com_username_preenchido_explica_que_so_o_professor_muda(token):
    _set_identity(github_username="anasilva")
    out: list[str] = []
    profile.run_perfil(print_fn=out.append)
    texto = "\n".join(out)
    assert "só pode ser" in texto
    assert "professor" in texto


def test_perfil_vazio_pergunta_e_salva(token, monkeypatch):
    _set_identity(github_username="")
    monkeypatch.setattr(profile, "is_interactive", lambda: True)
    salvos: list[tuple] = []
    monkeypatch.setattr(
        profile,
        "post_me_profile",
        lambda api, tok, nome, gh: salvos.append((nome, gh)) or {},
    )
    respostas = iter(["anasilva", "s"])
    out: list[str] = []
    rc = profile.run_perfil(input_fn=lambda _p: next(respostas), print_fn=out.append)
    assert rc == 0
    assert salvos == [("Ana Silva", "anasilva")]


def test_perfil_vazio_sem_tty_orienta_terminal_normal(token, monkeypatch):
    _set_identity(github_username="")
    monkeypatch.setattr(profile, "is_interactive", lambda: False)
    erros_out: list[str] = []
    rc = profile.run_perfil(print_fn=lambda _s: None, err_print=erros_out.append)
    assert rc == 2
    assert "terminal normal" in "\n".join(erros_out)


def test_perfil_sem_sessao_manda_logar(monkeypatch):
    import autograde_idp.auth as auth_mod

    monkeypatch.setattr(auth_mod, "load_token", lambda: None)
    erros_out: list[str] = []
    rc = profile.run_perfil(print_fn=lambda _s: None, err_print=erros_out.append)
    assert rc == 2
    assert "autograde login" in "\n".join(erros_out)


def test_perfil_traduz_erro_http_do_backend(token, monkeypatch):
    def boom(api, tok):
        raise profile.HttpError(403, '{"error":"not_in_roster"}')

    monkeypatch.setattr(profile, "fetch_me_identity", boom)
    erros_out: list[str] = []
    rc = profile.run_perfil(print_fn=lambda _s: None, err_print=erros_out.append)
    assert rc == 2
    assert "planilha da turma" in "\n".join(erros_out)
