"""Unit test for scripts/dedup_pages.py against a synthetic duplicated DB.

Builds a throwaway hera.db with one path carrying 3 rows (fts_map + vec rows
each, plus a citation on a loser), runs apply(), and asserts:
  - exactly one live pages row for that path (the oldest survivor)
  - the citation was re-pointed onto the survivor (no orphan)
  - pages/pages_fts_map/pages_vec counts are aligned for live pages
  - PRAGMA foreign_key_check is empty
  - a second apply() is a no-op (idempotent)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import dedup_pages  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "hera.db"
    monkeypatch.setattr(hera_db, "DB_PATH", db)
    monkeypatch.setattr(dedup_pages.hera_db, "DB_PATH", db)
    c = hera_db.ensure_ready(db)
    # pages_fts_map is created lazily by ingest.py/search.py, not in the base
    # schema — create it here so the synthetic fixture mirrors a real vault.
    c.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid   INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id)
    )""")
    yield c
    c.close()


def _add_page(c, page_id, path, created, body="body"):
    c.execute(
        "INSERT INTO pages(id, type, title, path, created_at, updated_at) "
        "VALUES (?, 'entity', 'Dup', ?, ?, ?)", (page_id, path, created, created))
    cur = c.execute("INSERT INTO pages_fts(title, body) VALUES ('Dup', ?)", (body,))
    c.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)", (cur.lastrowid, page_id))
    c.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
              (page_id, hera_db_pack([0.0] * 768)))


def hera_db_pack(vec):
    import embed
    return embed.pack(vec)


def test_apply_collapses_dupes_and_repoints_citations(conn):
    c = conn
    path = "wiki/entities/Dup.md"
    _add_page(c, "01A00000000000000000000000", path, "2026-01-01T00:00:00")  # oldest -> survivor
    _add_page(c, "01B00000000000000000000000", path, "2026-01-02T00:00:00")
    _add_page(c, "01C00000000000000000000000", path, "2026-01-03T00:00:00")
    # A citation attached to a loser must survive, re-pointed to the survivor.
    c.execute("INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
              "VALUES ('01C00000000000000000000000', 's1', 'final', 5, '2026-01-03T00:00:00')")
    c.commit()

    dedup_pages.apply(c)

    (n,) = c.execute("SELECT count(*) FROM pages WHERE path=? AND archived_at IS NULL", (path,)).fetchone()
    assert n == 1
    (survivor,) = c.execute("SELECT id FROM pages WHERE path=? AND archived_at IS NULL", (path,)).fetchone()
    assert survivor == "01A00000000000000000000000", "oldest row must survive"

    # Citation re-pointed onto survivor, none orphaned.
    orphan = c.execute(
        "SELECT count(*) FROM citations WHERE page_id NOT IN (SELECT id FROM pages)").fetchone()[0]
    assert orphan == 0
    on_survivor = c.execute(
        "SELECT count(*) FROM citations WHERE page_id=?", (survivor,)).fetchone()[0]
    assert on_survivor == 1

    # Index counts aligned for live pages.
    pages = c.execute("SELECT count(*) FROM pages WHERE archived_at IS NULL").fetchone()[0]
    fts = c.execute("SELECT count(*) FROM pages_fts_map m JOIN pages p ON p.id=m.page_id "
                    "WHERE p.archived_at IS NULL").fetchone()[0]
    vec = c.execute("SELECT count(*) FROM pages_vec v JOIN pages p ON p.id=v.page_id "
                    "WHERE p.archived_at IS NULL").fetchone()[0]
    assert pages == fts == vec == 1

    # FK integrity clean.
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []

    # Idempotent: a second apply removes nothing.
    groups_before = dedup_pages._duplicate_groups(c)
    assert groups_before == {}
    dedup_pages.apply(c)
    (n2,) = c.execute("SELECT count(*) FROM pages WHERE path=? AND archived_at IS NULL", (path,)).fetchone()
    assert n2 == 1
