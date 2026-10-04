#!/usr/bin/env python3
"""session_end_file.py — SessionEnd hook (design §8.4).

On session end, launch a background filing job that runs Single Source Ingest
against the session transcript with source_kind=session and ADR-11 explicit-
statement auto-resolution.

Contract:
  - Idempotent per session_id (filed_sessions bookkeeping table).
  - Async by default: the hook forks a background worker and returns fast.
  - Sync mode for tests: env var HERA_FILING_SYNC=1 forces inline execution.
  - Logs to ~/.hera/filing.log.

The filing "job" itself is `.venv/bin/python -m filing_worker <transcript> <session_id>`
which we implement here as a module-level function `run_filing(...)` — same
script can be invoked as CLI or imported as a function.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback


_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]
FILING_LOG = pathlib.Path(os.environ.get("HERA_FILING_LOG", REPO / ".hera" / "filing.log"))


def _log(msg: str) -> None:
    try:
        FILING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FILING_LOG.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _already_filed(conn, session_id: str) -> bool:
    row = conn.execute(
        "SELECT filed_at FROM filed_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row is not None


def _mark_filed(conn, session_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO filed_sessions(session_id, filed_at) VALUES (?, ?)",
        (session_id, time.strftime("%Y-%m-%dT%H:%M:%S")),
    )
    conn.commit()


HEADER = "# Session transcript "
CLAIM_STALE_S = 2 * 3600   # a worker that died mid-ingest frees its claim
RETRY_LIMIT = 3            # pending sessions retried per worker run


def _scratch_for(session_id: str) -> pathlib.Path:
    # Codex ids look like "codex:<uuid>"; ':' is illegal in Windows filenames.
    # The real id is kept in the file's first line, not its name.
    return REPO / ".hera" / f"session-{re.sub(r'[^A-Za-z0-9._-]', '_', session_id)}.md"


def _claim_path(session_id: str) -> pathlib.Path:
    return REPO / ".hera" / "claims" / f"{_scratch_for(session_id).stem}.claim"


def _claim(session_id: str) -> bool:
    """Exclusive per-session claim so concurrent workers never ingest the same
    session twice (SessionEnd of one session + retry sweep of another)."""
    p = _claim_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - p.stat().st_mtime > CLAIM_STALE_S:
            p.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def _release(session_id: str) -> None:
    try:
        _claim_path(session_id).unlink()
    except OSError:
        pass


def _ensure_embed_ready() -> None:
    """Best-effort: start the Ollama daemon if it isn't answering. The filing
    worker runs detached, so a few seconds' wait costs the user nothing; ingest
    itself re-checks and fails cleanly (session stays pending) if still down."""
    import embed  # type: ignore
    try:
        embed.embed("ready")
        return
    except Exception:
        pass
    try:
        sys.path.insert(0, str(REPO / "scripts" / "install"))
        import ollama_provision  # type: ignore
        ok, detail = ollama_provision.start_daemon()
        _log(f"ollama was down; start_daemon -> {ok}: {detail}")
    except Exception:
        _log("ollama start failed:\n" + traceback.format_exc())


def _distill(tp: pathlib.Path, session_id: str) -> pathlib.Path:
    """Flatten a transcript (user prompts + assistant text) into
    .hera/session-<id>.md, which ingest reads and retries re-use."""
    parts: list[str] = [f"{HEADER}{session_id}",
                        f"# Ingested at {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    for line in tp.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            entry = json.loads(s)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            text_parts = [c.get("text", "") for c in content
                          if isinstance(c, dict) and c.get("type") == "text"]
            if text_parts:
                parts.append(f"[{role}] " + "\n".join(text_parts))
    scratch = _scratch_for(session_id)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("\n\n".join(parts), encoding="utf-8")
    return scratch


def _file_scratch(conn, scratch: pathlib.Path, session_id: str) -> int:
    """Ingest a distilled session under its claim. 0 = filed (or already
    filed / claimed elsewhere), 1 = failed and left pending for a retry."""
    import ingest  # type: ignore
    if not _claim(session_id):
        _log(f"session {session_id} is being filed by another worker — skipping")
        return 0
    try:
        if _already_filed(conn, session_id):
            return 0
        _ensure_embed_ready()
        r = ingest.ingest_source(str(scratch), source_kind="session")
        _mark_filed(conn, session_id)
    except Exception:
        _log(f"ingest error (session {session_id} left pending for retry):\n"
             + traceback.format_exc())
        return 1
    finally:
        _release(session_id)
    _log(f"filed session={session_id} src={r.source.title!r} concepts={len(r.concepts)} entities={len(r.entities)} warnings={len(r.warnings)}")
    return 0


def run_filing(transcript_path: str, session_id: str) -> int:
    """Run the actual filing. Returns 0 on success, non-zero on error."""
    sys.path.insert(0, str(REPO / "scripts"))
    import hera_db  # type: ignore

    conn = hera_db.ensure_ready()
    if _already_filed(conn, session_id):
        _log(f"session {session_id} already filed — skipping")
        return 0

    tp = pathlib.Path(transcript_path)
    if not tp.exists():
        _log(f"transcript missing: {tp}")
        return 1

    try:
        scratch = _distill(tp, session_id)
    except Exception:
        _log("transcript flatten error:\n" + traceback.format_exc())
        return 1
    return _file_scratch(conn, scratch, session_id)


def _catch_up_score(transcript_path: str, session_id: str) -> None:
    """Score citations the async Stop hook may have missed. Stop does not run
    for every turn in practice; scoring is cursor-based, so this never double
    counts what Stop already recorded."""
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import hera_db  # type: ignore
        import stop_score  # type: ignore
        summary = stop_score._score(hera_db.ensure_ready(), session_id,
                                    pathlib.Path(transcript_path))
        _log(f"catch-up scored session={session_id} cursor={summary['cursor']} "
             f"tier2={summary['inserted']['final']}")
    except Exception:
        _log("catch-up scoring error:\n" + traceback.format_exc())


def _pending(conn) -> list[tuple[str, pathlib.Path]]:
    """Distilled sessions with no filed_sessions row, oldest first."""
    out = []
    for p in sorted((REPO / ".hera").glob("session-*.md"), key=lambda p: p.stat().st_mtime):
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                first = f.readline().strip()
        except OSError:
            continue
        if first.startswith(HEADER):
            sid = first[len(HEADER):].strip()
            if sid and not _already_filed(conn, sid):
                out.append((sid, p))
    return out


def retry_pending(limit: int = RETRY_LIMIT) -> int:
    """File up to ``limit`` sessions whose earlier filing failed (Ollama down,
    LLM quota hit, …). Returns how many were filed. Codex sessions are
    re-extracted with the Codex backend, as on their first attempt."""
    sys.path.insert(0, str(REPO / "scripts"))
    import hera_db  # type: ignore
    conn = hera_db.ensure_ready()
    filed = 0
    backend = os.environ.get("HERA_LLM_BACKEND")
    try:
        for sid, scratch in _pending(conn):
            if filed >= limit:
                break
            if _claim_path(sid).exists():
                continue
            if sid.startswith("codex:"):
                os.environ["HERA_LLM_BACKEND"] = "codex"
            elif backend is None:
                os.environ.pop("HERA_LLM_BACKEND", None)
            else:
                os.environ["HERA_LLM_BACKEND"] = backend
            if _file_scratch(conn, scratch, sid) == 0 and _already_filed(conn, sid):
                filed += 1
    finally:
        if backend is None:
            os.environ.pop("HERA_LLM_BACKEND", None)
        else:
            os.environ["HERA_LLM_BACKEND"] = backend
    return filed


def _read_event() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _hera_off() -> bool:
    v = os.environ.get("HERA_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _hera_off():
        return 0
    try:
        # CLI mode: `session_end_file.py <transcript> <session_id>`
        if len(sys.argv) >= 3:
            _catch_up_score(sys.argv[1], sys.argv[2])
            rc = run_filing(sys.argv[1], sys.argv[2])
            retry_pending()
            return rc

        # Hook mode: read event JSON from stdin.
        evt = _read_event()
        transcript_path = evt.get("transcript_path") or os.environ.get("HERA_TRANSCRIPT")
        session_id = evt.get("session_id") or os.environ.get("HERA_SESSION_ID") or "unknown"
        if not transcript_path:
            _log("no transcript_path in event; skipping")
            return 0

        if os.environ.get("HERA_FILING_SYNC") == "1":
            return run_filing(transcript_path, session_id)

        # Fire-and-forget: fork a detached background worker, per-OS.
        py = REPO / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        interp = str(py) if py.exists() else sys.executable
        cmd = [interp, __file__, transcript_path, session_id]
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — no POSIX session API.
            popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        _log("hook error:\n" + traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
