"""Codex event adaptation: only user/final text is filed and turns score once."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("codex_hook", REPO / "scripts" / "codex_hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def test_normalize_rollout_excludes_tools_and_nonfinal_messages(tmp_path):
    source = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "text", "text": "Remember this"}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "output": "private tool output"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "text", "text": "working"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final", "content": [{"type": "text", "text": "Final fact"}]}},
    ]
    source.write_text("".join(json.dumps(x) + "\n" for x in rows))
    dest = tmp_path / "normalized.jsonl"
    assert hook._normalize(source, dest) == 2
    text = dest.read_text()
    assert "Remember this" in text and "Final fact" in text
    assert "private tool output" not in text and "working" not in text


def test_stop_scores_each_codex_turn_once(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE pages(id TEXT, title TEXT, aliases TEXT)")
    conn.execute("CREATE TABLE citations(page_id TEXT, session_id TEXT, tier TEXT, points INTEGER, cited_at TEXT)")
    conn.execute("CREATE TABLE config(key TEXT, value TEXT)")
    conn.execute("INSERT INTO pages VALUES ('p1', 'Test Note', '[]')")
    conn.execute("INSERT INTO config VALUES ('points_final', '5')")
    monkeypatch.setitem(sys.modules, "hera_db", types.SimpleNamespace(ensure_ready=lambda: conn))
    event = {"session_id": "s1", "turn_id": "t1", "last_assistant_message": "(Source: [[Test Note]]) [[Test Note]]"}
    hook._score(event)
    hook._score(event)
    assert conn.execute("SELECT count(*), sum(points) FROM citations").fetchone() == (1, 5)
    hook._score(dict(event, turn_id="t2"))
    assert conn.execute("SELECT count(*) FROM citations").fetchone()[0] == 2
