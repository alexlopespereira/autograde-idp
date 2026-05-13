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


def _emoji_marks() -> tuple[str, str]:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in enc:
        return "✅", "❌"
    return "[OK]", "[FAIL]"


def render_bulletin(bulletin: dict[str, Any]) -> str:
    ok_mark, fail_mark = _emoji_marks()
    lines: list[str] = []
    for c in bulletin.get("criterios", []) or []:
        mark = ok_mark if c.get("passed") else fail_mark
        cid = c.get("id", "?")
        pe = c.get("points_earned", 0)
        pm = c.get("points_max", 0)
        msg = c.get("message", "") or ""
        lines.append(f"  {mark} {cid}  {pe}/{pm}  {msg}".rstrip())
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
    body = {
        "exercicio": exercise_id,
        "repo_url": repo_url,
        "shell_evidence": shell_evidence,
    }
    try:
        preview = grade_preview_call(api, bundle.access_token, body)
    except requests.RequestException as exc:
        err_print(f"erro de rede em /grade-preview: {exc}")
        return 3
    except HttpError as exc:
        err_print(f"/grade-preview falhou: HTTP {exc.status} {exc.text}")
        return 3
    except ValidarError as exc:
        err_print(f"erro: {exc}")
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
    try:
        result = submissions_call(api, bundle.access_token, submit_body)
    except requests.RequestException as exc:
        err_print(
            f"erro de rede em /submissions: {exc}. UUID preservado em "
            f"{path}; rode `autograde validar` novamente para retry."
        )
        return 3
    except HttpError as exc:
        if 400 <= exc.status < 500:
            try:
                clear_uuid(path, exercise_id)
            except InFlightLockedError:
                pass
            err_print(f"/submissions rejeitou: HTTP {exc.status} {exc.text}")
            return 3
        err_print(
            f"/submissions falhou (HTTP {exc.status}). UUID preservado para retry."
        )
        return 3
    except ValidarError as exc:
        err_print(f"erro: {exc}")
        return 3

    try:
        clear_uuid(path, exercise_id)
    except InFlightLockedError:
        pass

    written = result.get("written", False)
    sid = result.get("submission_id", submission_uuid)
    print_fn(f"Submetido. id={sid} written={written}")
    return 0
