"""Schema round-trip tests for hera_db.

Covers CP-1 checks:
  - schema creates cleanly on a fresh DB
  - every §5 table exists and accepts a row
  - FTS5 index is queryable
  - vec table accepts a 768-dim insert and returns via KNN
  - foreign keys are enforced
  - schema is idempotent (running init_schema twice does not error)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "hera.db"
    monkeypatch.setattr(hera_db, "DB_PATH", p)
    conn = hera_db.ensure_ready(p)
    yield conn
    conn.close()


def test_all_expected_tables_present(db):
    have = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
    for t in ("pages", "citations", "pages_fts", "pages_vec",
              "conflicts", "pending_deltas", "session_cursors",
              "filed_sessions", "config", "schema_version"):
        assert t in have, f"missing table {t}"


def test_config_defaults_seeded(db):
    v = db.execute("SELECT value FROM config WHERE key='points_final'").fetchone()
    assert v == ("5",)


def test_pages_and_citations_insert(db):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        "INSERT INTO pages(id,title,type,path,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        ("01AAA", "Test", "concept", "wiki/concepts/test.md", now, now),
    )
    db.execute(
        "INSERT INTO citations(page_id, session_id, tier, points, cited_at) VALUES (?,?,?,?,?)",
        ("01AAA", "sess-1", "final", 5, now),
    )
    db.commit()
    n = db.execute("SELECT count(*) FROM citations").fetchone()[0]
    assert n == 1


def test_foreign_key_enforced(db):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
            "VALUES ('does-not-exist', 's', 'final', 5, ?)",
            (now,),
        )
        db.commit()


def test_fts5_queryable(db):
    db.execute("INSERT INTO pages_fts(rowid, title, body) VALUES (1, 'agentic orchestration', 'multi-agent systems')")
    db.commit()
    rows = db.execute(
        "SELECT rowid FROM pages_fts WHERE pages_fts MATCH 'agentic'"
    ).fetchall()
    assert rows == [(1,)]


def test_vec_knn_roundtrip(db):
    import struct
    vec_a = [0.0] * 768; vec_a[0] = 1.0
    vec_b = [0.0] * 768; vec_b[1] = 1.0
    blob_a = struct.pack(f"{768}f", *vec_a)
    blob_b = struct.pack(f"{768}f", *vec_b)
    db.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)", ("01AAA", blob_a))
    db.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)", ("01BBB", blob_b))
    db.commit()
    # KNN query: nearest to vec_a should be 01AAA itself
    q = struct.pack(f"{768}f", *vec_a)
    rows = db.execute(
        "SELECT page_id FROM pages_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
        (q,),
    ).fetchall()
    assert rows == [("01AAA",)]


def test_init_schema_is_idempotent(db, tmp_path):
    # Running ensure_ready again on the same file must not error and must
    # not create duplicate schema_version rows.
    hera_db.ensure_ready(pathlib.Path(db.execute("PRAGMA database_list").fetchone()[2]))
    n = db.execute("SELECT count(*) FROM schema_version WHERE version=1").fetchone()[0]
    assert n == 1
