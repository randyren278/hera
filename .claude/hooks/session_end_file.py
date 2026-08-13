#!/usr/bin/env python3
"""session_end_file.py — SessionEnd hook (design §8.4).

On session end, launch a background filing job that runs Single Source Ingest
against the session transcript with source_kind=session and ADR-11 explicit-
statement auto-resolution.

Contract:
  - Idempotent per session_id (filed_sessions bookkeeping table).
  - Async by default: the hook forks a background worker and returns fast.
  - Sync mode for tests: env var BRAIN_FILING_SYNC=1 forces inline execution.
  - Logs to ~/.brain/filing.log.

The filing "job" itself is `.venv/bin/python -m filing_worker <transcript> <session_id>`
which we implement here as a module-level function `run_filing(...)` — same
script can be invoked as CLI or imported as a function.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import traceback


_env_vault = os.environ.get("SECOND_BRAIN_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]
FILING_LOG = pathlib.Path(os.environ.get("BRAIN_FILING_LOG", REPO / ".brain" / "filing.log"))


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


def run_filing(transcript_path: str, session_id: str) -> int:
    """Run the actual filing. Returns 0 on success, non-zero on error."""
    sys.path.insert(0, str(REPO / "scripts"))
    import brain_db  # type: ignore
    import ingest  # type: ignore

    conn = brain_db.ensure_ready()
    if _already_filed(conn, session_id):
        _log(f"session {session_id} already filed — skipping")
        return 0

    tp = pathlib.Path(transcript_path)
    if not tp.exists():
        _log(f"transcript missing: {tp}")
        return 1

    # Distill the transcript into a source document. We build a plain-text
    # concatenation of assistant final answers + user prompts and hand it to
    # the ingest engine as if it were an article. This deterministic pre-
    # processing keeps behavior predictable when the transcript is large or
    # contains tool_use noise.
    parts: list[str] = [f"# Session transcript {session_id}",
                        f"# Ingested at {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
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
    except Exception:
        _log("transcript flatten error:\n" + traceback.format_exc())
        return 1

    # Write the distilled transcript to .brain/session-<id>.md so ingest has a real
    # file to point at (and to preserve as raw source).
    scratch = REPO / ".brain" / f"session-{session_id}.md"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("\n\n".join(parts), encoding="utf-8")

    try:
        r = ingest.ingest_source(str(scratch), source_kind="session")
    except Exception:
        _log("ingest error:\n" + traceback.format_exc())
        return 1

    _mark_filed(conn, session_id)
    _log(f"filed session={session_id} src={r.source.title!r} concepts={len(r.concepts)} entities={len(r.entities)} warnings={len(r.warnings)}")
    return 0


def _read_event() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _brain_off() -> bool:
    v = os.environ.get("SECOND_BRAIN_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _brain_off():
        return 0
    try:
        # CLI mode: `session_end_file.py <transcript> <session_id>`
        if len(sys.argv) >= 3:
            return run_filing(sys.argv[1], sys.argv[2])

        # Hook mode: read event JSON from stdin.
        evt = _read_event()
        transcript_path = evt.get("transcript_path") or os.environ.get("BRAIN_TRANSCRIPT")
        session_id = evt.get("session_id") or os.environ.get("BRAIN_SESSION_ID") or "unknown"
        if not transcript_path:
            _log("no transcript_path in event; skipping")
            return 0

        if os.environ.get("BRAIN_FILING_SYNC") == "1":
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
