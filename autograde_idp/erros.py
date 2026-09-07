"""Tradução de erros do backend para instruções acionáveis.

Motivação (bug real): o aluno rodava `autograde validar ia-1.3` e recebia

    /grade-preview falhou: HTTP 403 {"error":"turma_not_eligible"}

Sem nada mais. A reação natural foi rodar `autograde login` — que não tem
relação nenhuma com o problema (turma vem do roster, não do login Google) —
e bater na mesma parede. Toda mensagem daqui pra frente responde três
perguntas na ordem: **o que aconteceu**, **por que**, **o que fazer agora**.

O registry local existe mesmo com o backend já mandando `message`: o backend
demora a ser redeployado, o aluno pode estar numa CLI nova contra um backend
antigo, e uma queda de rede não produz corpo JSON nenhum. Quando o backend
manda `message`, ele ganha (é quem conhece os valores concretos — a turma do
aluno, a data de abertura); o registry entra como título + passos.
"""

from __future__ import annotations

import json
import sys
import textwrap
from typing import Optional

FAQ_URL = "https://github.com/alexlopespereira/autograde-idp/blob/main/docs/FAQ.md"

WRAP_WIDTH = 76

# O console padrão do Windows (cp1252) levanta UnicodeEncodeError ao imprimir
# '✗', '⚠' ou '→'. Como boa parte da turma roda Windows nativo, todo símbolo
# passa por aqui — um crash no meio de uma mensagem de erro é o pior lugar
# possível pra descobrir isso. Resolvido em runtime (e não em constante) para
# respeitar o stdout injetado nos testes.
X = "✗"
AVISO = "⚠"
SETA = "→"
CERTO = "✅"
ERRADO = "❌"

_ASCII_FALLBACK = {X: "[X]", AVISO: "[!]", SETA: "->", CERTO: "[OK]", ERRADO: "[X]"}


def suporta_unicode() -> bool:
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


def sym(simbolo: str) -> str:
    """Devolve ``simbolo`` ou seu equivalente ASCII, conforme o console."""
    if suporta_unicode():
        return simbolo
    return _ASCII_FALLBACK.get(simbolo, simbolo)


class Explicacao:
    """Título humano + passos concretos para um código de erro do backend."""

    def __init__(self, titulo: str, passos: tuple[str, ...], anchor: str = "") -> None:
        self.titulo = titulo
        self.passos = passos
        self.anchor = anchor


# Chave = campo `error` do JSON do backend. Textos em PT-BR, imperativos, com
# o comando exato a rodar — nada de "verifique sua configuração".
REGISTRY: dict[str, Explicacao] = {
    "turma_not_eligible": Explicacao(
        "Seu cadastro não está na turma deste exercício.",
        (
            "Sua turma vem da PLANILHA DO ROSTER, não do seu login Google — "
            "`autograde login` não resolve isso.",
            "Rode `autograde whoami` e confira a linha `turma`.",
            "Se a turma estiver errada (ou faltar uma), peça ao professor para "
            "corrigir a coluna `turma` da sua linha. Ela aceita mais de uma "
            "turma separada por `;` — ex.: `TD-2026-01;IA-2026-01`.",
            "Se a turma está certa, você provavelmente digitou o id de outro "
            "curso: `ia-1.3` (Agentes de IA) e `1.3` (Transformação Digital) "
            "são exercícios diferentes.",
        ),
        "turma_not_eligible",
    ),
    "not_in_roster": Explicacao(
        "Seu email não está na planilha da turma.",
        (
            "Rode `autograde whoami` e veja com qual email você está logado.",
            "Causa mais comum: login com gmail pessoal no lugar do email "
            "institucional. Rode `autograde login` e escolha a conta certa.",
            "Se o email está certo, peça ao professor para incluí-lo no roster.",
        ),
        "not_in_roster",
    ),
    "repo_owner_mismatch": Explicacao(
        "Este repositório não é seu (segundo o roster).",
        (
            "Confira em que diretório você está: `git config --get remote.origin.url`.",
            "Confira o username do roster: `autograde perfil`.",
            "Se você criou o repo com outra conta GitHub, veja qual está ativa "
            "com `gh auth status`.",
            "Se o roster tem o username errado, peça a correção ao professor.",
        ),
        "repo_owner_mismatch",
    ),
    "exercise_not_open_yet": Explicacao(
        "O exercício ainda não abriu.",
        ("Não há nada a consertar do seu lado — volte na data de abertura.",),
        "exercise_not_open_yet",
    ),
    "exercise_not_found": Explicacao(
        "Não existe exercício com esse id.",
        (
            "Agentes de IA usa prefixo: `ia-1.1`, `ia-1.2`, `ia-1.3`, `ia-1.4`.",
            "Transformação Digital não usa prefixo: `1.1`, `2.1`, `4.1`.",
            "A lista completa está na pasta `exercicios/` do repositório do "
            "seu curso.",
        ),
        "exercise_not_found",
    ),
    "invalid_repo_url": Explicacao(
        "O `origin` deste diretório não aponta para um repo do GitHub.",
        (
            "Rode `git config --get remote.origin.url` para ver o valor atual.",
            "Entre no diretório do repositório do exercício e rode de novo.",
        ),
        "invalid_repo_url",
    ),
    "invalid_shell_evidence": Explicacao(
        "O backend recusou a evidência coletada pela CLI.",
        (
            "Quase sempre é CLI desatualizada. No diretório do autograde-idp: "
            "`git pull && pip install -e .`",
            "Depois confirme com `autograde --version` e tente de novo.",
        ),
        "invalid_shell_evidence",
    ),
    "respostas_missing": Explicacao(
        "Este exercício exige responder a pergunta de reflexão.",
        (
            "Rode `autograde validar <id>` num terminal interativo — sem pipe, "
            "sem redirect de stdin, sem rodar dentro do agente de IA.",
        ),
        "perguntas",
    ),
    "rate_limit_cooldown": Explicacao(
        "Tentativas muito seguidas.",
        ("Espere ~30 segundos e rode o mesmo comando. Nada foi perdido.",),
        "rate_limit",
    ),
    "rate_limit_daily_cap": Explicacao(
        "Você atingiu o limite de tentativas de hoje neste exercício.",
        (
            "O contador zera à meia-noite (horário de Brasília).",
            "Suas submissões anteriores continuam valendo — a MAIOR nota conta.",
        ),
        "rate_limit",
    ),
    "github_unavailable": Explicacao(
        "O backend não conseguiu ler seu repositório no GitHub.",
        (
            "O repositório precisa ser PÚBLICO: Settings > General > Danger "
            "Zone > Change visibility > Public.",
            "Confirme que ele existe e não foi renomeado: `gh repo view`.",
            "Se estiver tudo certo, foi instabilidade do GitHub — tente de novo.",
        ),
        "github_unavailable",
    ),
    "roster_unavailable": Explicacao(
        "O backend não conseguiu ler a planilha da turma.",
        ("É um problema do servidor. Tente em alguns minutos e avise o professor.",),
        "erro_5xx",
    ),
    "sheets_drop_detected": Explicacao(
        "A planilha não confirmou a gravação da sua nota.",
        (
            "Rode `autograde validar <id>` de novo — a CLI reusa o mesmo id de "
            "submissão, então não vai duplicar sua linha.",
        ),
        "erro_5xx",
    ),
    "invalid_token": Explicacao(
        "Sua sessão não foi aceita.",
        ("Rode `autograde login` para renovar.",),
        "token_expired",
    ),
    "missing_authorization": Explicacao(
        "Você não está logado.",
        ("Rode `autograde login`.",),
        "token_expired",
    ),
    "invalid_github_username": Explicacao(
        "Username do GitHub inválido.",
        ("Use só o login, sem `@` e sem URL. Ex.: `anasilva`.",),
        "",
    ),
}

# Prefixos: o backend usa `rate_limit_*` e `rate_limit_preview_*` para o mesmo
# problema visto pelo aluno.
_ALIASES = {
    "rate_limit_preview_cooldown": "rate_limit_cooldown",
    "rate_limit_preview_daily_cap": "rate_limit_daily_cap",
    "respostas_count_mismatch": "respostas_missing",
    "resposta_empty": "respostas_missing",
}


# Corpo de erro que não é JSON (página HTML de proxy, stack trace) só serve
# para o aluno copiar no pedido de ajuda — 500 chars bastam. Um corpo JSON,
# porém, NÃO pode ser cortado: `message` hoje passa de 500 chars, e um JSON
# truncado deixa de parsear, some com o código do erro e derruba a explicação
# inteira para o caminho genérico ("o backend recusou a permissão"). Foi o que
# aconteceu na prática assim que as mensagens acionáveis entraram no ar.
LIMITE_CORPO_OPACO = 500
LIMITE_CORPO_JSON = 8000


def truncar_corpo(text: str) -> str:
    """Limita o corpo de uma resposta de erro sem quebrar o JSON dentro dele."""
    if not text:
        return ""
    if len(text) <= LIMITE_CORPO_OPACO:
        return text
    try:
        json.loads(text)
    except (ValueError, TypeError):
        return text[:LIMITE_CORPO_OPACO]
    return text[:LIMITE_CORPO_JSON]


def parse_error_body(text: str) -> tuple[str, str]:
    """Extrai ``(codigo, message)`` do corpo JSON. Tolera corpo não-JSON."""
    if not text:
        return "", ""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return "", text.strip()
    if not isinstance(data, dict):
        return "", text.strip()
    return str(data.get("error", "") or ""), str(data.get("message", "") or "")


def _wrap(text: str, indent: str) -> list[str]:
    out: list[str] = []
    for paragraph in text.splitlines() or [text]:
        # URL é a única coisa aqui que o aluno precisa copiar inteira; quebrar
        # `.../autograde-idp/blob/...` no hífen a torna inútil. Estourar a
        # margem é preferível a entregar um link que não funciona.
        chunks = textwrap.wrap(
            paragraph,
            width=WRAP_WIDTH,
            break_on_hyphens=False,
            break_long_words=False,
        )
        for chunk in chunks or [""]:
            out.append(f"{indent}{chunk}")
    return out


def explicar_http(
    status: int,
    body_text: str,
    *,
    acao: str = "A operação",
) -> str:
    """Bloco legível para uma resposta HTTP de erro do backend.

    ``acao`` é o que o aluno estava tentando fazer ("Validar o exercício",
    "Buscar suas notas") — vira a primeira linha, para o erro fazer sentido
    sem o aluno precisar reconstruir o contexto.
    """
    codigo, message = parse_error_body(body_text)
    exp = REGISTRY.get(_ALIASES.get(codigo, codigo))

    lines: list[str] = [""]
    titulo = exp.titulo if exp else _titulo_generico(status)
    lines.append(f"{sym(X)} {acao} não deu certo: {titulo}")

    # O backend conhece os valores concretos (sua turma, a data de abertura);
    # o registry conhece o roteiro. Mostrar os dois, sem duplicar.
    if message:
        lines.append("")
        lines.extend(_wrap(message, "  "))

    if exp and exp.passos:
        lines.append("")
        lines.append("  O que fazer:")
        for i, passo in enumerate(exp.passos, start=1):
            wrapped = _wrap(passo, "     ")
            wrapped[0] = f"  {i}. " + wrapped[0].lstrip()
            lines.extend(wrapped)
    elif not message:
        lines.append("")
        lines.extend(_wrap(_passos_genericos(status), "  "))

    anchor = exp.anchor if exp else ""
    if anchor and "FAQ.md" not in message:
        lines.append("")
        lines.append(f"  FAQ: {FAQ_URL}#{anchor}")

    lines.append("")
    lines.append(f"  (código: {codigo or 'sem código'} · HTTP {status})")
    return "\n".join(lines)


def _titulo_generico(status: int) -> str:
    if status == 401:
        return "sua sessão não foi aceita."
    if status == 403:
        return "o backend recusou a permissão."
    if status == 404:
        return "o backend não encontrou o recurso."
    if status == 429:
        return "você atingiu um limite de tentativas."
    if status >= 500:
        return "o servidor falhou."
    return "o backend recusou a requisição."


def _passos_genericos(status: int) -> str:
    if status >= 500:
        return (
            "Isso é um problema do servidor, não seu. Tente de novo em alguns "
            f"minutos; se persistir, avise o professor. FAQ: {FAQ_URL}#erro_5xx"
        )
    return f"Consulte a FAQ: {FAQ_URL}"


def explicar_rede(exc: object, *, acao: str = "A operação") -> str:
    """Bloco para falha de rede (sem resposta HTTP)."""
    return "\n".join(
        [
            "",
            f"{sym(X)} {acao} não deu certo: não consegui falar com o servidor.",
            "",
            *_wrap(str(exc), "  "),
            "",
            "  O que fazer:",
            "  1. Confira sua conexão com a internet.",
            "  2. Se estiver em rede corporativa/VPN, ela pode estar bloqueando "
            "o backend.",
            "  3. Tente de novo em alguns minutos — nada foi perdido.",
            "",
            f"  FAQ: {FAQ_URL}#erro_de_rede",
        ]
    )


def dica(texto: str) -> str:
    """Aviso não-fatal, formatado igual aos erros para não parecer ruído."""
    return "\n".join(["", f"{sym(AVISO)} {texto}"])


def explicar_sem_repo(exercise_id: Optional[str]) -> str:
    """Erro mais comum de quem não leu a documentação: diretório errado."""
    alvo = exercise_id or "<id>"
    return "\n".join(
        [
            "",
            f"{sym(X)} Você não está no diretório do repositório do exercício.",
            "",
            "  O `autograde` descobre qual repo avaliar lendo o `origin` do git",
            "  deste diretório — e aqui não há repositório git com `origin`.",
            "",
            "  O que fazer:",
            "  1. `cd` para a pasta que foi criada quando você clonou o repo",
            "     (ela contém uma subpasta `.git`).",
            "  2. Confirme com: git config --get remote.origin.url",
            f"  3. Rode de novo: autograde validar {alvo}",
            "",
            "  Se você ainda não clonou o repositório:",
            "     gh repo clone SEU-USUARIO/NOME-DO-REPO",
            "",
            f"  FAQ: {FAQ_URL}#nao_estou_num_repo",
        ]
    )
