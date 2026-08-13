"""Regression guard for the duplicate-page bug in scripts/ingest.py.

Bug: ingest mints a fresh ULID for every concept/entity PageWrite. When a page
already exists at the target path WITHOUT a contradiction (the common case), the
per-page loop fell through to _upsert_page, which dedups only on ON CONFLICT(id).
The id was brand-new, so a SECOND pages row was inserted at the same path (path
has no unique constraint), duplicating the FTS + vec index. N re-ingests -> N rows.

Fix: reuse the existing page's ULID (pw.id = old_id) before the write, so the
upsert UPDATES in place. This test ingests the same non-conflicting source twice
and asserts exactly one `pages` row per concept/entity path — it FAILS on the
buggy code (two rows) and PASSES once the id is reused.

Mirrors the throwaway-vault fixture pattern in test_ingest_conflicts.py.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402
import ingest  # noqa: E402
import conflicts  # noqa: E402


@pytest.fixture
def vault(tmp_path, monkeypatch):
    db = tmp_path / "brain.db"
    wiki = tmp_path / "wiki"
    monkeypatch.setattr(brain_db, "DB_PATH", db)
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest, "WIKI", wiki)
    monkeypatch.setattr(conflicts, "REPO", tmp_path)
    for sub in ("concepts", "entities", "sources", ".raw/articles"):
        (wiki / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ingest._embed, "embed", lambda text: [0.0] * 768)
    conn = brain_db.ensure_ready(db)
    yield conn, tmp_path, monkeypatch
    conn.close()


def _stub_extract(monkeypatch, concept_title, entity_title):
    """Extractor returns a source + one concept + one entity. No contradiction
    detector is stubbed to fire — the second ingest hits the non-conflicting
    existing-page branch, which is where the duplicate row was minted."""
    payload = {
        "source": {"title": "Playwright Brief", "one_line": "a brief",
                   "key_takeaways": ["k1"], "body": "summary"},
        "concepts": [{"title": concept_title, "one_line": "a concept",
                      "body": "concept body", "aliases": []}],
        "entities": [{"title": entity_title, "kind": "tool", "one_line": "a tool",
                      "body": "entity body", "aliases": []}],
        "warnings": [],
    }
    monkeypatch.setattr(ingest, "_call_claude_extract", lambda raw: payload)
    # No contradiction on the second pass — this is the common re-ingest case.
    monkeypatch.setattr(ingest, "_detect_contradiction", lambda old, new: None)


def _write_source_file(root, name="brief.txt"):
    src = root / "wiki" / ".raw" / "articles" / name
    src.write_text("Playwright is a browser automation tool.", encoding="utf-8")
    return src


def test_reingest_non_conflicting_keeps_one_row_per_path(vault):
    """Ingesting the same non-conflicting source twice must leave exactly one
    `pages` row (and one fts/vec row) per concept/entity path."""
    conn, root, mp = vault
    _stub_extract(mp, concept_title="Route Mocking", entity_title="Playwright")
    src = _write_source_file(root)

    ingest.ingest_source(str(src), source_kind="file", conn=conn)
    ingest.ingest_source(str(src), source_kind="file", conn=conn)

    for rel in ("wiki/concepts/Route Mocking.md", "wiki/entities/Playwright.md"):
        (n,) = conn.execute(
            "SELECT count(*) FROM pages WHERE path=? AND archived_at IS NULL", (rel,),
        ).fetchone()
        assert n == 1, f"expected one pages row for {rel}, found {n}"

    # Index alignment: fts_map and vec must not carry orphan/duplicate rows for
    # the surviving pages either.
    pages = conn.execute(
        "SELECT count(*) FROM pages WHERE archived_at IS NULL").fetchone()[0]
    fts = conn.execute(
        "SELECT count(*) FROM pages_fts_map m JOIN pages p ON p.id=m.page_id "
        "WHERE p.archived_at IS NULL").fetchone()[0]
    vec = conn.execute(
        "SELECT count(*) FROM pages_vec v JOIN pages p ON p.id=v.page_id "
        "WHERE p.archived_at IS NULL").fetchone()[0]
    assert pages == fts == vec, f"index drift: pages={pages} fts={fts} vec={vec}"
