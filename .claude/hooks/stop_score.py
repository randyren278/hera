#!/usr/bin/env python3
"""stop_score.py — async citation scorer (design §8.3, ADR-03).

Fires on Stop with async: true. Reads transcript_path from the event JSON,
resumes parsing from the last recorded cursor for this session_id, extracts
citations from the assistant's final message, and inserts `citations` rows.

Tiers (from design):
  - tier 2 (final answer, 5 pts): (Source: [[Title]]) and bare [[Title]]
    wikilinks in the assistant's final message
  - tier 1 (thinking, 1 pt): title mentions in `thinking` blocks
    — DISABLED per R-1 verdict on Claude Code 2.1.201 (see
    wiki/meta/r1-verdict.md). Flip TIER1_ENABLED to True when the transcript
    starts persisting thinking content parseably.

Dedup: a page cited N times in one turn scores at most once per tier per turn.
Cursor: stored in session_cursors keyed on session_id.
Failures: never raise (async hooks can't block anyway); every exception is
written to ~/.hera/scorer.log — the SessionStart hook / --doctor surface
fresh errors so weeks of silent scoreboard death cannot happen.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import traceback


_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]
SCORER_LOG = pathlib.Path(os.environ.get("HERA_SCORER_LOG", REPO / ".hera" / "scorer.log"))
TIER1_ENABLED = False  # see wiki/meta/r1-verdict.md

# Citation regexes.
# Tier 2 primary: `(Source: [[Title]])` — the canonical form from /hera-query.
SOURCE_RE = re.compile(r"\(Source:\s*\[\[([^\]]+)\]\]\)")
# Tier 2 secondary: bare `[[Title]]` anywhere in the final message.
WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")


def _log(msg: str) -> None:
    try:
        SCORER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SCORER_LOG.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _read_event() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _iter_transcript(path: pathlib.Path, start_line: int):
    """Yield (line_num, message_dict) for each valid JSON line from start_line onward."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i < start_line:
                    continue
                s = line.strip()
                if not s:
                    continue
                try:
                    yield i, json.loads(s)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def _assistant_text(msg: dict) -> str:
    """Extract the final answer text from an assistant message. Text blocks are
    concatenated; tool_use/tool_result are ignored — we score final answers."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def _thinking_text(msg: dict) -> str:
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "thinking":
                # Different providers use "text" or "thinking" as the key.
                parts.append(c.get("text") or c.get("thinking") or "")
        return "\n".join(parts)
    return ""


def _resolve_title(conn, title: str) -> str | None:
    row = conn.execute("SELECT id FROM pages WHERE title = ? COLLATE NOCASE",
                       (title.strip(),)).fetchone()
    if row:
        return row[0]
    # Alias lookup — pages.aliases is a JSON list.
    for pid, aliases in conn.execute("SELECT id, aliases FROM pages").fetchall():
        try:
            arr = json.loads(aliases or "[]")
        except Exception:
            arr = []
        if title.strip().lower() in (str(a).lower() for a in arr):
            return pid
    return None


def _score(conn, session_id: str, transcript_path: pathlib.Path) -> dict:
    """Score every message from cursor forward. Returns a summary dict."""
    row = conn.execute("SELECT cursor FROM session_cursors WHERE session_id = ?",
                       (session_id,)).fetchone()
    cursor = row[0] if row else 0

    points_thinking = int(conn.execute(
        "SELECT value FROM config WHERE key='points_thinking'").fetchone()[0])
    points_final = int(conn.execute(
        "SELECT value FROM config WHERE key='points_final'").fetchone()[0])

    new_cursor = cursor
    inserted = {"final": 0, "thinking": 0}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Per-turn dedup: (turn_id, page_id, tier)
    seen: set[tuple[int, str, str]] = set()

    for line_num, entry in _iter_transcript(transcript_path, cursor):
        new_cursor = line_num + 1
        # Only score assistant messages.
        etype = entry.get("type")
        if etype != "assistant":
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        # Tier 2: (Source: [[Title]]) + [[Title]] in final answer text.
        final_text = _assistant_text(msg)
        if final_text:
            titles = set(SOURCE_RE.findall(final_text)) | set(WIKILINK_RE.findall(final_text))
            for t in titles:
                pid = _resolve_title(conn, t)
                if not pid:
                    continue
                key = (line_num, pid, "final")
                if key in seen:
                    continue
                seen.add(key)
                conn.execute(
                    "INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
                    "VALUES (?, ?, 'final', ?, ?)",
                    (pid, session_id, points_final, now),
                )
                inserted["final"] += 1

        # Tier 1: thinking blocks (only if enabled per R-1 verdict).
        if TIER1_ENABLED:
            think_text = _thinking_text(msg)
            if think_text:
                for pid, title in conn.execute("SELECT id, title FROM pages").fetchall():
                    if re.search(r"\b" + re.escape(title) + r"\b", think_text, re.I):
                        key = (line_num, pid, "thinking")
                        if key in seen:
                            continue
                        seen.add(key)
                        conn.execute(
                            "INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
                            "VALUES (?, ?, 'thinking', ?, ?)",
                            (pid, session_id, points_thinking, now),
                        )
                        inserted["thinking"] += 1

    conn.execute(
        "INSERT INTO session_cursors(session_id, cursor, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
        (session_id, new_cursor, now),
    )
    conn.commit()
    return {"cursor": new_cursor, "inserted": inserted}


def _hera_off() -> bool:
    v = os.environ.get("HERA_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _hera_off():
        return 0
    try:
        evt = _read_event()
        transcript_path = evt.get("transcript_path") or os.environ.get("HERA_TRANSCRIPT")
        session_id = evt.get("session_id") or os.environ.get("HERA_SESSION_ID") or "unknown"
        if not transcript_path:
            _log("no transcript_path in event; skipping")
            return 0
        tp = pathlib.Path(transcript_path)
        if not tp.exists():
            _log(f"transcript not found: {tp}")
            return 0

        sys.path.insert(0, str(REPO / "scripts"))
        import hera_db  # type: ignore
        conn = hera_db.ensure_ready()
        summary = _score(conn, session_id, tp)
        _log(f"scored session={session_id} cursor={summary['cursor']} "
             f"tier2={summary['inserted']['final']} tier1={summary['inserted']['thinking']}")
    except Exception:
        _log("scorer error:\n" + traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
