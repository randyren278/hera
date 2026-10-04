#!/usr/bin/env python3
"""Adapt Codex lifecycle events to Hera's shared vault engines."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import time
from contextlib import redirect_stdout

VAULT = pathlib.Path(__file__).resolve().parents[1]
HOOKS = VAULT / ".claude" / "hooks"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event() -> dict:
    try:
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def _shared_context(name: str, event: dict) -> str:
    saved = sys.stdin
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        with redirect_stdout(out):
            _module(name).main()
    finally:
        sys.stdin = saved
    return out.getvalue()


def _score(event: dict) -> None:
    text = event.get("last_assistant_message")
    session = event.get("session_id")
    turn = event.get("turn_id")
    if not isinstance(text, str) or not session or not turn:
        return
    sys.path.insert(0, str(VAULT / "scripts"))
    import hera_db
    scorer = _module("stop_score")
    conn = hera_db.ensure_ready()
    conn.execute("CREATE TABLE IF NOT EXISTS codex_scored_turns "
                 "(session_id TEXT NOT NULL, turn_id TEXT NOT NULL, "
                 "PRIMARY KEY(session_id, turn_id))")
    if conn.execute("SELECT 1 FROM codex_scored_turns WHERE session_id=? AND turn_id=?",
                    (session, turn)).fetchone():
        return
    titles = set(scorer.SOURCE_RE.findall(text)) | set(scorer.WIKILINK_RE.findall(text))
    points = int(conn.execute("SELECT value FROM config WHERE key='points_final'").fetchone()[0])
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for title in titles:
        page = scorer._resolve_title(conn, title)
        if page:
            conn.execute("INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
                         "VALUES (?, ?, 'final', ?, ?)", (page, session, points, now))
    conn.execute("INSERT INTO codex_scored_turns VALUES (?, ?)", (session, turn))
    conn.commit()


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content
                         if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"))
    return ""


def _normalize(transcript: pathlib.Path, destination: pathlib.Path) -> int:
    """Keep only user prompts and final assistant messages from a Codex rollout.

    Codex warns that transcript structure can change, so an unrecognized entry
    is skipped; never pass tool output or private reasoning to ingestion.
    """
    messages = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "response_item":
            continue
        item = entry.get("payload") or {}
        if item.get("type") != "message":
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and item.get("phase") != "final":
            continue
        body = _text(item.get("content"))
        if body.strip():
            messages.append({"type": role, "message": {"role": role, "content": body}})
    if not messages:
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(item) + "\n" for item in messages), encoding="utf-8")
    return len(messages)


def _file(transcript: str, session: str) -> None:
    source = pathlib.Path(transcript)
    if not source.is_file():
        return
    normalized = VAULT / ".hera" / f"codex-{session}.jsonl"
    if _normalize(source, normalized):
        os.environ["HERA_LLM_BACKEND"] = "codex"
        filing = _module("session_end_file")
        filing.run_filing(str(normalized), f"codex:{session}")
        filing.retry_pending()


def main() -> int:
    if os.environ.get("HERA_OFF", "").strip().lower() not in ("", "0", "false", "no", "off"):
        return 0
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "file" and len(sys.argv) >= 4:
        _file(sys.argv[2], sys.argv[3])
        return 0
    event = _event()
    try:
        if action == "start":
            sys.stdout.write(_shared_context("session_start", event))
        elif action == "prompt":
            context = _shared_context("prompt_inject", event)
            if context:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}))
        elif action == "stop":
            _score(event)
        elif action == "end" and event.get("transcript_path") and event.get("session_id"):
            if os.environ.get("HERA_FILING_SYNC") == "1":
                _file(event["transcript_path"], event["session_id"])
            else:
                detach = ({"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | NEW_GROUP
                          if os.name == "nt" else {"start_new_session": True})
                subprocess.Popen([sys.executable, __file__, "file", event["transcript_path"], event["session_id"]],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **detach)
    except Exception as exc:
        log = VAULT / ".hera" / "codex-hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {action}: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
