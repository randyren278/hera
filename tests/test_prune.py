"""prune/restore round-trip, including a crash between the DB update and the
file move (the only non-atomic window)."""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import ingest  # noqa: E402
import prune  # noqa: E402


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(prune, "REPO", tmp_path)
    monkeypatch.setattr(prune, "ARCHIVE", tmp_path / "wiki" / ".archive")
    monkeypatch.setattr(prune, "LOG", tmp_path / "wiki" / "log.md")
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest._embed, "embed", lambda t, **k: [1.0] + [0.0] * 767)
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    page = tmp_path / "wiki" / "entities" / "Old Tool.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nid: P1\n---\nAn old tool.\n")
    conn.execute("INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at) "
                 "VALUES ('P1','Old Tool','[]','entity','wiki/entities/Old Tool.md',"
                 "'2020-01-01T00:00:00','2020-01-01T00:00:00')")
    ingest._index_page_search(conn, ingest.PageWrite(
        id="P1", title="Old Tool", type="entity", path=page, body_md="An old tool."))
    conn.commit()
    cand = prune.Candidate(page_id="P1", title="Old Tool", type_="entity",
                           path="wiki/entities/Old Tool.md", created_at="2020-01-01T00:00:00",
                           total_points=0) if hasattr(prune, "Candidate") else None
    return conn, page, cand


def _archived(conn):
    return conn.execute("SELECT archived_at FROM pages WHERE id='P1'").fetchone()[0]


def test_prune_then_restore_round_trips(vault):
    conn, page, cand = vault
    prune.prune(conn, [cand], dry_run=False)
    assert not page.exists() and _archived(conn)
    prune.restore(conn, "P1")
    assert page.exists() and _archived(conn) is None
    assert conn.execute("SELECT count(*) FROM pages_vec WHERE page_id='P1'").fetchone()[0] == 1


def test_crash_before_the_move_is_recoverable(vault, monkeypatch):
    conn, page, cand = vault
    monkeypatch.setattr(shutil, "move", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        prune.prune(conn, [cand], dry_run=False)
    monkeypatch.undo()
    # Either nothing happened, or restore can bring the page fully back.
    if _archived(conn):
        prune.restore(conn, "P1")
    assert page.exists() and _archived(conn) is None
    assert conn.execute("SELECT count(*) FROM pages_vec WHERE page_id='P1'").fetchone()[0] == 1


class _CrashOnArchiveUpdate:
    """Connection proxy that dies right after the file move, before the DB
    records the archive — the window prune cannot make atomic."""
    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __enter__(self):
        return self._c.__enter__()

    def __exit__(self, *a):
        return self._c.__exit__(*a)

    def execute(self, sql, *a):
        if sql.startswith("DELETE FROM pages_vec"):
            raise OSError("crash after move")
        return self._c.execute(sql, *a)


def test_crash_after_the_move_is_recoverable(vault):
    conn, page, cand = vault
    with pytest.raises(OSError):
        prune.prune(_CrashOnArchiveUpdate(conn), [cand], dry_run=False)
    conn.rollback()
    assert not page.exists()  # the file is in .archive, the DB never heard
    prune.restore(conn, "P1")
    assert page.exists() and _archived(conn) is None
