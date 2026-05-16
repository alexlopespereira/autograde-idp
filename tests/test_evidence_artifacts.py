"""Testes do collector de evidência de artefatos textuais."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from autograde_idp.evidence.artifacts import (
    CONTENT_MAX_CHARS,
    MAX_HEADINGS,
    MAX_LINKS,
    ArtifactSpec,
    collect_artifacts_evidence,
    collect_for_exercise,
    specs_for_exercise,
)

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$")


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_absent_required_artifact_returns_exists_false(tmp_path: Path) -> None:
    specs = [ArtifactSpec(path="missing.md", role="x")]
    results = collect_artifacts_evidence(specs, tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.exists is False
    assert r.size_bytes == 0
    assert r.word_count == 0
    assert r.sha256 == ""
    assert r.headings == []
    assert r.links == []
    assert r.content == ""
    assert r.truncated is False
    assert r.required is True
    assert r.role == "x"
    assert ISO_RE.match(r.captured_at)


def test_present_artifact_captures_content_and_metadata(tmp_path: Path) -> None:
    text = (
        "# Título\n\n"
        "Olá mundo, este é um teste.\n\n"
        "## Seção 2\n\n"
        "Veja https://example.com e também http://outro.test/path?q=1.\n"
    )
    _write(tmp_path, "A_meta_prompt.md", text)
    specs = [ArtifactSpec(path="A_meta_prompt.md", role="meta_prompt")]
    [r] = collect_artifacts_evidence(specs, tmp_path)

    assert r.exists is True
    assert r.size_bytes == len(text.encode("utf-8"))
    assert r.word_count >= 12
    assert r.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert r.headings == ["# Título", "## Seção 2"]
    assert r.links == ["https://example.com", "http://outro.test/path?q=1"]
    assert r.content == text
    assert r.truncated is False
    assert r.role == "meta_prompt"


def test_to_dict_omits_optional_fields_when_default(tmp_path: Path) -> None:
    _write(tmp_path, "x.md", "# H\n\nfoo bar")
    [r] = collect_artifacts_evidence([ArtifactSpec(path="x.md")], tmp_path)
    d = r.to_dict()
    assert d["tool"] == "artifacts"
    assert d["path"] == "x.md"
    assert d["exists"] is True
    assert "truncated" not in d  # default False → omitido
    assert "role" not in d  # role=None → omitido


def test_to_dict_includes_role_and_truncated_when_set(tmp_path: Path) -> None:
    long_text = "x" * (CONTENT_MAX_CHARS + 10)
    _write(tmp_path, "big.md", long_text)
    [r] = collect_artifacts_evidence(
        [ArtifactSpec(path="big.md", role="report_ai_1")], tmp_path
    )
    d = r.to_dict()
    assert d["role"] == "report_ai_1"
    assert d["truncated"] is True
    assert len(d["content"]) == CONTENT_MAX_CHARS


def test_truncation_preserves_full_text_metrics(tmp_path: Path) -> None:
    long_text = ("palavra " * (CONTENT_MAX_CHARS // 2)).rstrip()
    _write(tmp_path, "big.md", long_text)
    [r] = collect_artifacts_evidence([ArtifactSpec(path="big.md")], tmp_path)
    assert r.truncated is True
    assert len(r.content) == CONTENT_MAX_CHARS
    # word_count e sha256 são calculados em cima do texto FULL, não do truncado
    assert r.word_count == CONTENT_MAX_CHARS // 2
    assert r.sha256 == hashlib.sha256(long_text.encode("utf-8")).hexdigest()
    assert r.size_bytes == len(long_text.encode("utf-8"))


def test_path_traversal_attempt_returns_absent(tmp_path: Path) -> None:
    # Mesmo se o arquivo existir fora do root, o collector deve recusar.
    outside = tmp_path.parent / "secret.md"
    outside.write_text("segredo", encoding="utf-8")
    try:
        specs = [ArtifactSpec(path="../secret.md")]
        [r] = collect_artifacts_evidence(specs, tmp_path)
        assert r.exists is False
        assert r.content == ""
    finally:
        outside.unlink(missing_ok=True)


def test_links_dedup_and_strip_trailing_punctuation(tmp_path: Path) -> None:
    text = (
        "Veja https://a.test/x.\n"
        "E https://a.test/x novamente.\n"
        "Mais um: https://b.test/y;\n"
    )
    _write(tmp_path, "f.md", text)
    [r] = collect_artifacts_evidence([ArtifactSpec(path="f.md")], tmp_path)
    assert r.links == ["https://a.test/x", "https://b.test/y"]


def test_headings_cap_respected(tmp_path: Path) -> None:
    headings = "\n".join(f"# H{i}" for i in range(MAX_HEADINGS + 20))
    _write(tmp_path, "many.md", headings)
    [r] = collect_artifacts_evidence([ArtifactSpec(path="many.md")], tmp_path)
    assert len(r.headings) == MAX_HEADINGS


def test_links_cap_respected(tmp_path: Path) -> None:
    urls = "\n".join(f"https://example.test/{i}" for i in range(MAX_LINKS + 20))
    _write(tmp_path, "manylinks.md", urls)
    [r] = collect_artifacts_evidence([ArtifactSpec(path="manylinks.md")], tmp_path)
    assert len(r.links) == MAX_LINKS


def test_invalid_utf8_does_not_raise(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    p.write_bytes(b"# H\n\n\xff\xfe not utf8")
    [r] = collect_artifacts_evidence([ArtifactSpec(path="bad.md")], tmp_path)
    assert r.exists is True
    assert r.size_bytes == p.stat().st_size
    assert "# H" in r.content  # replace mode mantém o resto legível


def test_specs_for_exercise_21_lists_all_six_artifacts() -> None:
    specs = specs_for_exercise("2.1")
    paths = [s.path for s in specs]
    assert paths == [
        "A_meta_prompt.md",
        "B_relatorio_assistente1.md",
        "B_relatorio_assistente2.md",
        "B_sintese_adversarial.md",
        "C_grill_transcript.md",
        "C_mapa_atores.md",
    ]
    assert all(s.required for s in specs)
    roles = [s.role for s in specs]
    assert roles == [
        "meta_prompt",
        "report_ai_1",
        "report_ai_2",
        "synthesis",
        "grill_transcript",
        "actor_map",
    ]


def test_specs_for_exercise_unknown_id_returns_empty() -> None:
    assert specs_for_exercise("1.1") == []
    assert specs_for_exercise("9.9") == []


def test_collect_for_exercise_21_returns_six_results_even_when_files_missing(
    tmp_path: Path,
) -> None:
    results = collect_for_exercise("2.1", tmp_path)
    assert len(results) == 6
    assert all(not r.exists for r in results)
    assert {r.role for r in results} == {
        "meta_prompt",
        "report_ai_1",
        "report_ai_2",
        "synthesis",
        "grill_transcript",
        "actor_map",
    }


def test_collect_for_exercise_21_happy_path(tmp_path: Path) -> None:
    _write(tmp_path, "A_meta_prompt.md", "# Meta\nprompt aqui")
    _write(tmp_path, "B_relatorio_assistente1.md", "# R1\ncorpo")
    _write(tmp_path, "B_relatorio_assistente2.md", "# R2\ncorpo")
    _write(tmp_path, "B_sintese_adversarial.md", "# Sintese\ncorpo")
    _write(tmp_path, "C_grill_transcript.md", "# Grill\ncorpo")
    _write(tmp_path, "C_mapa_atores.md", "# Mapa\ncorpo")

    results = collect_for_exercise("2.1", tmp_path)
    assert len(results) == 6
    assert all(r.exists for r in results)
    payload = [r.to_dict() for r in results]
    expected_keys = {
        "tool",
        "path",
        "required",
        "exists",
        "size_bytes",
        "word_count",
        "sha256",
        "headings",
        "links",
        "content",
        "captured_at",
        "role",
    }
    for entry in payload:
        assert expected_keys.issubset(entry.keys())
        assert entry["tool"] == "artifacts"


@pytest.mark.parametrize("eid", ["1.1", "1.2", "1.3", "1.4", "3.0"])
def test_collect_for_exercise_unknown_id_returns_empty(
    eid: str, tmp_path: Path
) -> None:
    assert collect_for_exercise(eid, tmp_path) == []
