"""Session filing survives transient failures (Ollama down, LLM quota hit).

Regression: a failed SessionEnd ingest was logged and then forgotten — the
distilled .hera/session-*.md stayed on disk but nothing ever filed it, and the
embed failure struck AFTER the source page was written (orphan page, no index).
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
import embed  # noqa: E402
import hera_db  # noqa: E402


@pytest.fixture
def filing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERA_VAULT", str(tmp_path))
    monkeypatch.setenv("HERA_FILING_LOG", str(tmp_path / ".hera" / "filing.log"))
    import session_end_file
    sef = importlib.reload(session_end_file)
    db = tmp_path / "hera.db"
    monkeypatch.setattr(hera_db, "ensure_ready", lambda *a, **k: _init(db))
    calls: list[str] = []
    state = {"fail": True, "exc": embed.EmbedError("ollama down")}

    def fake_ingest(path, source_kind="file", **_):
        calls.append(pathlib.Path(path).name)
        if state["fail"]:
            raise state["exc"]
        return types.SimpleNamespace(source=types.SimpleNamespace(title="t"),
                                     concepts=[], entities=[], warnings=[])

    import ingest
    monkeypatch.setattr(ingest, "ingest_source", fake_ingest)
    monkeypatch.setattr(sef, "_ensure_embed_ready", lambda: None)
    return sef, tmp_path, calls, state, db


def _init(db):
    conn = hera_db.connect(db)
    hera_db.init_schema(conn)
    return conn


def _transcript(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """A session with enough conversation to be worth filing."""
    p = tmp_path / f"{name}.jsonl"
    p.write_text("".join(json.dumps({"message": {"role": r, "content": t * 40}}) + "\n"
                         for r, t in (("user", "please explain the build. "),
                                      ("assistant", "the build compiles then links. "))))
    return p


def _filed(db) -> set[str]:
    return {r[0] for r in _init(db).execute("SELECT session_id FROM filed_sessions")}


def test_failed_session_is_retried_later(filing):
    sef, tmp, calls, state, db = filing
    assert sef.run_filing(str(_transcript(tmp, "a")), "sess-a") == 1
    assert _filed(db) == set()

    state["fail"] = False
    assert sef.run_filing(str(_transcript(tmp, "b")), "sess-b") == 0
    assert sef.retry_pending() == 1
    assert _filed(db) == {"sess-a", "sess-b"}
    assert sef.retry_pending() == 0  # nothing left; filed sessions never re-ingest


def test_scratch_name_is_portable_but_id_is_preserved(filing):
    sef, tmp, calls, state, db = filing
    assert sef.run_filing(str(_transcript(tmp, "c")), "codex:01a1-xyz") == 1  # stays pending
    names = [p.name for p in (tmp / ".hera").glob("session-*.md")]
    assert names and all(":" not in n for n in names), names  # Windows-safe
    state["fail"] = False
    assert sef.retry_pending() == 1
    assert _filed(db) == {"codex:01a1-xyz"}  # the real id, read from the header


def test_retry_respects_limit_and_claims(filing):
    sef, tmp, calls, state, db = filing
    for i in range(4):
        sef.run_filing(str(_transcript(tmp, f"t{i}")), f"s{i}")
    state["fail"] = False
    assert sef._claim("s0") is True  # another worker is filing s0 right now
    assert sef.retry_pending(limit=2) == 2
    assert "s0" not in _filed(db)
    sef._release("s0")
    assert sef.retry_pending() == 2
    assert _filed(db) == {"s0", "s1", "s2", "s3"}


def test_ingest_checks_embedder_before_writing(tmp_path, monkeypatch):
    """With Ollama down, ingest must fail before the LLM call or any page write."""
    import ingest
    importlib.reload(ingest)
    src = tmp_path / "s.md"
    src.write_text("some source text")
    monkeypatch.setattr(ingest, "WIKI", tmp_path / "wiki")
    monkeypatch.setattr(ingest._embed, "embed",
                        lambda t: (_ for _ in ()).throw(embed.EmbedError("down")))
    monkeypatch.setattr(ingest, "_call_claude_extract",
                        lambda raw: pytest.fail("LLM called with embedder down"))
    with pytest.raises(embed.EmbedError):
        ingest.ingest_source(str(src), conn=_init(tmp_path / "x.db"),
                             raw_dir=tmp_path / "raw")
    assert not (tmp_path / "wiki").exists()
    assert not (tmp_path / "raw").exists()


def test_pending_lists_only_failed_sessions(filing):
    sef, tmp, calls, state, db = filing
    sef.run_filing(str(_transcript(tmp, "d")), "sess-d")
    assert [sid for sid, _ in sef._pending(_init(db))] == ["sess-d"]


def test_session_end_catches_up_citations_a_missed_stop_hook_dropped(filing):
    """Stop is async and is sometimes never run; SessionEnd re-scores from the
    cursor so final-answer citations still reach the ranking."""
    sef, tmp, calls, state, db = filing
    conn = _init(db)
    conn.execute("INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at) "
                 "VALUES ('P1','Answer Bank','[]','concept','wiki/concepts/a.md','t','t')")
    conn.commit()
    tp = tmp / "t.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Done. (Source: [[Answer Bank]])"}]}}) + "\n")
    sef._catch_up_score(str(tp), "sess-x")
    sef._catch_up_score(str(tp), "sess-x")  # cursor-based: never double counts
    rows = _init(db).execute("SELECT page_id, tier FROM citations").fetchall()
    assert rows == [("P1", "final")]


def test_filed_scratch_files_are_cleaned_up(filing):
    """A filed session's distilled copy is redundant (ingest keeps the raw copy
    under wiki/.raw/), so .hera/ must not grow forever."""
    sef, tmp, calls, state, db = filing
    state["fail"] = False
    sef.run_filing(str(_transcript(tmp, "e")), "sess-e")
    stale = tmp / ".hera" / "session-old.md"
    stale.write_text("# Session transcript sess-old\n")
    _init(db).execute("INSERT INTO filed_sessions VALUES ('sess-old','t')").connection.commit()
    sef.retry_pending()
    assert not list((tmp / ".hera").glob("session-*.md"))


def test_logs_rotate(filing, monkeypatch):
    sef, tmp, *_ = filing
    monkeypatch.setattr(sef, "LOG_MAX_BYTES", 100)
    for _ in range(20):
        sef._log("x" * 20)
    log = tmp / ".hera" / "filing.log"
    assert log.stat().st_size <= 200 and log.with_suffix(".log.1").exists()


def test_near_empty_session_is_marked_filed_without_an_llm_call(filing):
    """Sessions like `/model` then exit produced 'Empty Session Transcript' pages."""
    sef, tmp, calls, state, db = filing
    state["fail"] = False
    tiny = tmp / "tiny.jsonl"
    tiny.write_text(json.dumps({"message": {"role": "user", "content": "/model"}}) + "\n")
    assert sef.run_filing(str(tiny), "sess-tiny") == 0
    assert calls == [] and _filed(db) == {"sess-tiny"}


def test_retries_stop_after_max_attempts(filing):
    sef, tmp, calls, state, db = filing
    state["exc"] = ValueError("extraction returned invalid JSON")  # permanent
    sef.run_filing(str(_transcript(tmp, "bad")), "sess-bad")  # attempt 1
    for _ in range(sef.MAX_ATTEMPTS + 3):
        sef.retry_pending()
    assert len(calls) == sef.MAX_ATTEMPTS
    assert _filed(db) == set()


def test_transient_failures_do_not_use_up_attempts(filing):
    """An Ollama outage or LLM quota across many session ends must not make a
    session give up for good (council round 2, R2-7)."""
    sef, tmp, calls, state, db = filing  # fake ingest raises EmbedError (transient)
    sef.run_filing(str(_transcript(tmp, "t")), "sess-t")
    for _ in range(sef.MAX_ATTEMPTS + 2):
        sef.retry_pending()
    assert sef._attempts("sess-t") == 0
    assert sef._is_transient(RuntimeError("codex extraction failed: exit 1\nERROR: "
                                          "You've hit your usage limit."))
    assert not sef._is_transient(ValueError("extraction returned invalid JSON"))
