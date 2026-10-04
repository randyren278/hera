"""reindex_orphans: lists by default, indexes from frontmatter with --apply."""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import ingest  # noqa: E402
import reindex_orphans  # noqa: E402


def test_list_then_apply(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest, "WIKI", tmp_path / "wiki")
    monkeypatch.setattr(ingest._embed, "embed", lambda t, **k: [1.0] + [0.0] * 767)
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    monkeypatch.setattr(hera_db, "ensure_ready", lambda *a, **k: conn)
    page = tmp_path / "wiki" / "concepts" / "Reciprocal Rank Fusion.md"
    page.parent.mkdir(parents=True)
    text = ('---\nid: 01ORPHAN\ntype: concept\ntitle: "Reciprocal Rank Fusion"\n'
            'aliases: ["RRF"]\n---\n> [!info] Fuse ranked lists.\n')
    page.write_text(text)

    assert reindex_orphans.main([]) == 0
    assert "1 orphan page" in capsys.readouterr().out
    assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == 0

    assert reindex_orphans.main(["--apply"]) == 0
    row = conn.execute("SELECT id, title, aliases FROM pages").fetchone()
    assert row[0] == "01ORPHAN" and row[1] == "Reciprocal Rank Fusion" and "RRF" in row[2]
    assert conn.execute("SELECT count(*) FROM pages_vec").fetchone()[0] == 1
    assert page.read_text() == text  # Markdown untouched
    assert reindex_orphans.orphans(conn) == []
