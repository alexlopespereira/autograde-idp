"""autograde validar <exercicio_id> — comando principal do CLI.

Detecta repo via `git config --get remote.origin.url`, coleta evidências
(US-13 amplia), chama POST /grade-preview, mostra boletim, prompt s/n,
e POST /submissions com submission_uuid persistido em ~/.git-exercicios/
in-flight.json. Lock via fcntl/msvcrt protege contra 2 terminais paralelos
(R4).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import requests

from autograde_idp.auth import (
    AuthError,
    TokenAgeExceededError,
    TokenBundle,
    TokenExpiredError,
    config_dir,
    ensure_fresh_token,
    load_token,
)
from autograde_idp.evidence import artifacts as artifacts_mod
from autograde_idp.evidence.shell import collect_for_exercise

IN_FLIGHT_FILENAME = "in-flight.json"
DEFAULT_API_URL = "https://autograde-backend-1065810445001.southamerica-east1.run.app"
MARKER_FILENAME = ".autograde-exercise"


class ValidarError(Exception):
    """Erro de validação — propagado ao CLI como exit !=0."""


class InFlightLockedError(ValidarError):
    """Outro processo autograde validar segura o lock."""


class HttpError(ValidarError):
    """Resposta HTTP não-200 do backend."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text
        super().__init__(f"HTTP {status}: {text}")


def in_flight_path(base_dir: Optional[Path] = None) -> Path:
    base = base_dir if base_dir is not None else config_dir()
    return base / IN_FLIGHT_FILENAME


def detect_repo_url(cwd: Optional[Path] = None) -> str:
    """Lê remote.origin.url via git. Levanta ValidarError se ausente."""
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidarError(f"git falhou: {exc}") from exc
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        raise ValidarError("Não está num repo git com remote origin")
    return url


def discover_exercise_id(cwd: Optional[Path] = None) -> str:
    """Heurística mínima: lê marcador .autograde-exercise.

    Em ambiguidade (sem marcador), levanta ValidarError pedindo id
    explícito — não chuta um exercício pra não submeter no errado.
    """
    root = cwd or Path.cwd()
    marker = root / MARKER_FILENAME
    if marker.is_file():
        eid = marker.read_text(encoding="utf-8").strip()
        if eid:
            return eid
    raise ValidarError(
        "Não foi possível detectar o exercício automaticamente. "
        "Informe o id (ex: `autograde validar 1.1`)."
    )


def _acquire_lock(f: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(f: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def in_flight_locked(path: Path) -> Iterator[Any]:
    """Abre in-flight.json com lock exclusivo não-bloqueante.

    Se outro processo já segura o lock, levanta InFlightLockedError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    f = open(path, "r+", encoding="utf-8")
    try:
        _acquire_lock(f)
    except (BlockingIOError, OSError) as exc:
        f.close()
        raise InFlightLockedError(
            "Outra execução de autograde validar está rodando neste perfil — aguarde"
        ) from exc
    try:
        yield f
    finally:
        try:
            _release_lock(f)
        finally:
            f.close()


def _load_dict(f: Any) -> dict[str, str]:
    f.seek(0)
    content = f.read()
    if not content.strip():
        return {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_dict(f: Any, data: dict[str, str]) -> None:
    f.seek(0)
    f.truncate()
    json.dump(data, f, indent=2)
    f.flush()


def get_or_create_uuid(path: Path, exercise_id: str) -> str:
    """Retorna uuid persistido pra exercise_id ou gera+persiste um novo."""
    with in_flight_locked(path) as f:
        data = _load_dict(f)
        existing = data.get(exercise_id)
        if existing:
            return existing
        new_uuid = uuid.uuid4().hex
        data[exercise_id] = new_uuid
        _save_dict(f, data)
        return new_uuid


def clear_uuid(path: Path, exercise_id: str) -> None:
    """Remove a key do dict (chamado após 200 ou 4xx final)."""
    with in_flight_locked(path) as f:
        data = _load_dict(f)
        if exercise_id in data:
            del data[exercise_id]
            _save_dict(f, data)


REPO_MAP_FILENAME = "repo-map.json"


def repo_map_path(base_dir: Optional[Path] = None) -> Path:
    base = base_dir if base_dir is not None else config_dir()
    return base / REPO_MAP_FILENAME


def _load_repo_map(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[str(k)] = [str(u) for u in v if isinstance(u, str)]
    return out


def detect_repo_mismatch(
    exercise_id: str,
    repo_url: str,
    map_path: Optional[Path] = None,
) -> Optional[str]:
    """Se repo_url foi usado pra OUTRO exercício, retorna esse exercise_id.

    None = sem conflito (primeira vez OU repo já usado pra este exercício).
    """
    data = _load_repo_map(map_path or repo_map_path())
    for ex_id, urls in data.items():
        if ex_id == exercise_id:
            continue
        if repo_url in urls:
            return ex_id
    return None


def remember_repo(
    exercise_id: str, repo_url: str, map_path: Optional[Path] = None
) -> None:
    """Adiciona (exercise_id, repo_url) ao mapa local. Dedupe."""
    p = map_path or repo_map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _load_repo_map(p)
    urls = data.get(exercise_id, [])
    if repo_url not in urls:
        urls.append(repo_url)
        data[exercise_id] = urls
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _emoji_marks() -> tuple[str, str]:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in enc:
        return "✅", "❌"
    return "[OK]", "[FAIL]"


def collect_respostas(
    perguntas: list[dict[str, Any]],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[str]:
    """Pede ao aluno cada pergunta subjetiva. Loop até resposta não-vazia.

    Sempre síncrono e interativo — exercício com perguntas força resposta
    (decisão de produto). EOFError sobe pro chamador tratar (ex: pipe sem TTY).
    """
    if not perguntas:
        return []
    print_fn("")
    print_fn(f"Perguntas ({len(perguntas)}) — responda antes de submeter:")
    respostas: list[str] = []
    for idx, pergunta in enumerate(perguntas):
        texto = str(pergunta.get("texto", "")).strip() or "(sem texto)"
        print_fn(f"\n  [{idx + 1}/{len(perguntas)}] {texto}")
        while True:
            raw = input_fn("  Resposta: ")
            answer = raw.strip()
            if answer:
                respostas.append(answer)
                break
            print_fn("  Resposta não pode ser vazia. Tente de novo.")
    print_fn("")
    return respostas


FEEDBACK_INLINE_MAX = 50  # acima disso, msg quebra pra linha indentada
FEEDBACK_WRAP_WIDTH = 76


def render_bulletin(bulletin: dict[str, Any]) -> str:
    import textwrap

    ok_mark, fail_mark = _emoji_marks()
    lines: list[str] = []
    for c in bulletin.get("criterios", []) or []:
        mark = ok_mark if c.get("passed") else fail_mark
        pe = c.get("points_earned", 0)
        pm = c.get("points_max", 0)
        msg = (c.get("message", "") or "").strip()
        header = f"  {mark} {pe}/{pm}"
        if not msg:
            lines.append(header)
        elif len(msg) <= FEEDBACK_INLINE_MAX and "\n" not in msg:
            lines.append(f"{header}  {msg}")
        else:
            lines.append(header)
            for paragraph in msg.splitlines() or [msg]:
                wrapped = textwrap.wrap(paragraph, width=FEEDBACK_WRAP_WIDTH) or [""]
                for chunk in wrapped:
                    lines.append(f"      {chunk}")
    total = bulletin.get("total", 0)
    max_total = bulletin.get("max_total", 0)
    lines.append("")
    lines.append(f"  Total: {total}/{max_total}")
    return "\n".join(lines)


def render_preview(preview: dict[str, Any]) -> str:
    body = render_bulletin(preview.get("bulletin", {}) or {})
    late = bool(preview.get("late"))
    dias = preview.get("dias_apos_recomendado", 0)
    suffix = f"\n  (Atraso: {dias} dia(s))" if late else ""
    return f"Boletim:\n{body}{suffix}"


def api_url() -> str:
    return os.environ.get("AUTOGRADE_API_URL", DEFAULT_API_URL).rstrip("/")


def _post(api: str, path: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{api}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            raise ValidarError(f"resposta inválida de {path}: {exc}") from exc
    text = (resp.text or "")[:500]
    raise HttpError(resp.status_code, text)


def grade_preview_call(api: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    return _post(api, "/grade-preview", token, body)


def submissions_call(api: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    return _post(api, "/submissions", token, body)


def _load_fresh_bundle() -> TokenBundle:
    bundle = load_token()
    if bundle is None:
        raise ValidarError("Sem sessão ativa. Rode `autograde login`.")
    return ensure_fresh_token(bundle, api_url())


def run_validar(
    exercise_id: Optional[str],
    *,
    auto_submit: bool = False,
    cwd: Optional[Path] = None,
    in_flight: Optional[Path] = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    err_print: Optional[Callable[[str], None]] = None,
) -> int:
    """Orquestra o fluxo validar e retorna o exit code do CLI."""
    if err_print is None:

        def err_print(s: str) -> None:  # type: ignore[misc]
            print(s, file=sys.stderr)

    try:
        repo_url = detect_repo_url(cwd)
    except ValidarError as exc:
        err_print(f"erro: {exc}")
        return 2

    if not exercise_id:
        try:
            exercise_id = discover_exercise_id(cwd)
        except ValidarError as exc:
            err_print(f"erro: {exc}")
            return 2

    conflict_ex = detect_repo_mismatch(exercise_id, repo_url)
    if conflict_ex is not None:
        print_fn(
            f"\n⚠️  Aviso: este repo ({repo_url}) foi usado anteriormente "
            f"para o exercício {conflict_ex}.\n"
            f"   Você está rodando `autograde validar {exercise_id}` — "
            f"certifique-se de estar no diretório certo.\n"
        )
        if not auto_submit:
            try:
                confirm = input_fn(
                    f"Continuar com {exercise_id} neste repo mesmo assim? (s/n): "
                ).strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "s":
                print_fn("Cancelado. Mude pro diretório certo e rode novamente.")
                return 2

    try:
        bundle = _load_fresh_bundle()
    except TokenAgeExceededError as exc:
        err_print(str(exc))
        return 2
    except TokenExpiredError as exc:
        err_print(f"sessão expirada: {exc}. Rode `autograde login` novamente.")
        return 2
    except (AuthError, ValidarError) as exc:
        err_print(f"erro: {exc}")
        return 2

    path = in_flight if in_flight is not None else in_flight_path()
    try:
        submission_uuid = get_or_create_uuid(path, exercise_id)
    except InFlightLockedError as exc:
        err_print(f"erro: {exc}")
        return 2

    api = api_url()
    shell_results = collect_for_exercise(exercise_id, repo_url)
    shell_evidence = [r.to_dict() for r in shell_results]
    artifact_results = artifacts_mod.collect_for_exercise(
        exercise_id, cwd if cwd is not None else Path.cwd()
    )
    artifacts_evidence = [r.to_dict() for r in artifact_results]
    body = {
        "exercicio": exercise_id,
        "repo_url": repo_url,
        "shell_evidence": shell_evidence,
        "artifacts_evidence": artifacts_evidence,
    }
    try:
        preview = grade_preview_call(api, bundle.id_token, body)
    except requests.RequestException as exc:
        err_print(f"erro de rede em /grade-preview: {exc}")
        return 3
    except HttpError as exc:
        err_print(f"/grade-preview falhou: HTTP {exc.status} {exc.text}")
        return 3
    except ValidarError as exc:
        err_print(f"erro: {exc}")
        return 3

    perguntas = preview.get("perguntas") or []
    respostas: list[str] = []
    if perguntas:
        # Coleta respostas ANTES de renderizar o bulletin — segunda call do
        # /grade-preview retorna bulletin já com a nota da reflexao do Gemini.
        try:
            respostas = collect_respostas(perguntas, input_fn=input_fn, print_fn=print_fn)
        except EOFError:
            err_print(
                "perguntas exigem resposta interativa; rode sem pipe/redirect ou "
                "responda no terminal."
            )
            return 2
        try:
            preview = grade_preview_call(api, bundle.id_token, dict(body, respostas=respostas))
        except requests.RequestException as exc:
            err_print(f"erro de rede em /grade-preview: {exc}")
            return 3
        except HttpError as exc:
            if exc.status == 429:
                err_print(
                    f"limite de previews atingido (HTTP 429): {exc.text}. "
                    "Aguarde cooldown ou volte amanhã (reset à meia-noite BRT)."
                )
                return 3
            err_print(f"/grade-preview falhou: HTTP {exc.status} {exc.text}")
            return 3

    print_fn(render_preview(preview))

    if auto_submit:
        choice = "s"
    else:
        try:
            choice = input_fn("Deseja submeter? (s/n): ").strip().lower()
        except EOFError:
            choice = "n"

    if choice != "s":
        print_fn("Submissão cancelada.")
        return 0

    submit_body = dict(body, submission_uuid=submission_uuid)
    if respostas:
        submit_body["respostas"] = respostas
    try:
        result = submissions_call(api, bundle.id_token, submit_body)
    except requests.RequestException as exc:
        err_print(
            f"erro de rede em /submissions: {exc}. UUID preservado em "
            f"{path}; rode `autograde validar` novamente para retry."
        )
        return 3
    except HttpError as exc:
        if exc.status == 429:
            err_print(
                f"limite atingido (HTTP 429): {exc.text}. UUID preservado para "
                f"retry depois do cooldown/janela diária."
            )
            return 3
        if 400 <= exc.status < 500:
            try:
                clear_uuid(path, exercise_id)
            except InFlightLockedError:
                pass
            err_print(f"/submissions rejeitou: HTTP {exc.status} {exc.text}")
            return 3
        err_print(f"/submissions falhou (HTTP {exc.status}). UUID preservado para retry.")
        return 3
    except ValidarError as exc:
        err_print(f"erro: {exc}")
        return 3

    try:
        clear_uuid(path, exercise_id)
    except InFlightLockedError:
        pass

    # Memoriza repo usado por este exercício pra warning futuro.
    try:
        remember_repo(exercise_id, repo_url)
    except OSError:
        pass  # falha de disco não-bloqueante

    written = result.get("written", False)
    sid = result.get("submission_id", submission_uuid)
    print_fn(f"Submetido. id={sid} written={written}")
    return 0
