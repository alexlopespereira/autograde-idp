"""Coleta de evidências de artefatos textuais entregues pelo aluno.

Lê arquivos markdown (ou texto) do repo local — meta-prompts, relatórios de
deep research, transcripts de sessão `/grill-me`, mapas de atores — e devolve
estruturas serializáveis para envio ao backend no campo ``artifacts_evidence``
de ``/grade-preview`` e ``/submissions``.

Separa duas dimensões:
  * **Métricas determinísticas locais** (existência, tamanho, contagem de
    palavras, headings, URLs, sha256) — backend usa para nota objetiva sem
    custo de LLM e para detecção de cópia entre turmas.
  * **Conteúdo truncado** — backend usa para LLM-as-judge contra rubrica.

Nunca levanta: arquivos ausentes viram :class:`ArtifactResult` com
``exists=False``. Mantém a mesma garantia do ``evidence/shell.py``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTENT_MAX_CHARS = 32_768  # ~6k palavras por arquivo; LLM judge ainda cabe
MAX_HEADINGS = 50
MAX_LINKS = 50

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s)\]<>\"']+", re.IGNORECASE)


@dataclass
class ArtifactSpec:
    """Descreve um arquivo esperado para um exercício."""

    path: str  # relativo à raiz do repo do aluno
    required: bool = True
    role: Optional[str] = None  # tag livre (ex: "meta_prompt", "report_ai_1")


@dataclass
class ArtifactResult:
    path: str
    role: Optional[str]
    required: bool
    exists: bool
    size_bytes: int
    word_count: int
    sha256: str
    headings: List[str]
    links: List[str]
    content: str
    truncated: bool
    captured_at: str

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "tool": "artifacts",
            "path": self.path,
            "required": self.required,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "word_count": self.word_count,
            "sha256": self.sha256,
            "headings": self.headings,
            "links": self.links,
            "content": self.content,
            "captured_at": self.captured_at,
        }
        if self.role is not None:
            d["role"] = self.role
        if self.truncated:
            d["truncated"] = True
        return d


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _absent(spec: ArtifactSpec) -> ArtifactResult:
    return ArtifactResult(
        path=spec.path,
        role=spec.role,
        required=spec.required,
        exists=False,
        size_bytes=0,
        word_count=0,
        sha256="",
        headings=[],
        links=[],
        content="",
        truncated=False,
        captured_at=_now_iso_utc(),
    )


def _extract_headings(text: str) -> List[str]:
    out: List[str] = []
    for m in _HEADING_RE.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        out.append(f"{'#' * level} {title}")
        if len(out) >= MAX_HEADINGS:
            break
    return out


def _extract_links(text: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= MAX_LINKS:
            break
    return out


def _count_words(text: str) -> int:
    return sum(1 for _ in re.finditer(r"\b\w+\b", text, flags=re.UNICODE))


def _read_one(spec: ArtifactSpec, root: Path) -> ArtifactResult:
    target = (root / spec.path).resolve()
    try:
        root_resolved = root.resolve()
        target.relative_to(root_resolved)
    except (OSError, ValueError):
        # path traversal (ex: "../etc/passwd") — trata como ausente
        return _absent(spec)

    if not target.is_file():
        return _absent(spec)

    try:
        raw_bytes = target.read_bytes()
    except OSError:
        return _absent(spec)

    sha = hashlib.sha256(raw_bytes).hexdigest()
    size_bytes = len(raw_bytes)
    try:
        full_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        full_text = raw_bytes.decode("utf-8", errors="replace")

    truncated = len(full_text) > CONTENT_MAX_CHARS
    content = full_text[:CONTENT_MAX_CHARS] if truncated else full_text

    return ArtifactResult(
        path=spec.path,
        role=spec.role,
        required=spec.required,
        exists=True,
        size_bytes=size_bytes,
        word_count=_count_words(full_text),
        sha256=sha,
        headings=_extract_headings(full_text),
        links=_extract_links(full_text),
        content=content,
        truncated=truncated,
        captured_at=_now_iso_utc(),
    )


def collect_artifacts_evidence(
    specs: List[ArtifactSpec],
    root: Path,
) -> List[ArtifactResult]:
    """Lê cada artefato em ``specs`` a partir de ``root``.

    Nunca levanta — arquivos ausentes/ilegíveis viram :class:`ArtifactResult`
    com ``exists=False`` (igual ao ``shell.py`` para `gh` ausente).
    """
    return [_read_one(s, root) for s in specs]


def specs_for_exercise(exercise_id: str) -> List[ArtifactSpec]:
    """Lista hardcoded de artefatos esperados por exercício.

    Mantém o mesmo padrão de ``commands_for_exercise`` em ``shell.py`` —
    cada exercício é declarado aqui; backend tem a rubrica.
    """
    if exercise_id == "2.1":
        return [
            ArtifactSpec(path="A_meta_prompt.md", role="meta_prompt"),
            ArtifactSpec(path="B_relatorio_assistente1.md", role="report_ai_1"),
            ArtifactSpec(path="B_relatorio_assistente2.md", role="report_ai_2"),
            ArtifactSpec(path="B_sintese_adversarial.md", role="synthesis"),
            ArtifactSpec(path="C_grill_transcript.md", role="grill_transcript"),
            ArtifactSpec(path="C_mapa_atores.md", role="actor_map"),
        ]
    return []


def collect_for_exercise(
    exercise_id: str,
    root: Path,
) -> List[ArtifactResult]:
    """Coleta artefatos aplicáveis ao ``exercise_id`` a partir de ``root``."""
    return collect_artifacts_evidence(specs_for_exercise(exercise_id), root)
