"""Conflict resolution is single-shot and keeps the search index current."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import conflicts  # noqa: E402
import hera_db  # noqa: E402
import ingest  # noqa: E402


@pytest.fixture
def conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(conflicts, "REPO", tmp_path)
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest._embed, "embed", lambda t, **k: [1.0] + [0.0] * 767)
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    page = tmp_path / "wiki" / "concepts" / "Tool.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nid: P1\ntitle: \"Tool\"\n---\nThe tool uses port 80.\n")
    conn.execute("INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at) "
                 "VALUES ('P1','Tool','[]','concept','wiki/concepts/Tool.md','t','t')")
    ingest._index_page_search(conn, ingest.PageWrite(
        id="P1", title="Tool", type="concept", path=page, body_md="The tool uses port 80."))
    conn.execute("INSERT INTO conflicts(page_id,claim_old,claim_new,detected_at) "
                 "VALUES ('P1','uses port 80','uses port 8443','t')")
    conn.commit()
    cid = conn.execute("SELECT id FROM conflicts").fetchone()[0]
    return conn, cid, page


def _fts_body(conn):
    return conn.execute("SELECT f.body FROM pages_fts f JOIN pages_fts_map m "
                        "ON m.rowid = f.rowid WHERE m.page_id='P1'").fetchone()[0]


@pytest.mark.parametrize("resolve", [conflicts.resolve_new, conflicts.resolve_both])
def test_resolution_reindexes_the_page(conflict, resolve):
    conn, cid, page = conflict
    resolve(conn, cid)
    assert "8443" in page.read_text()
    assert "8443" in _fts_body(conn), "search index still holds the pre-resolution body"


@pytest.mark.parametrize("resolve", [conflicts.resolve_new, conflicts.resolve_both,
                                     conflicts.resolve_old, conflicts.dismiss])
def test_resolving_twice_is_refused_and_changes_nothing(conflict, resolve):
    conn, cid, page = conflict
    resolve(conn, cid)
    text = page.read_text()
    with pytest.raises(SystemExit):
        conflicts.resolve_new(conn, cid)
    assert page.read_text() == text
