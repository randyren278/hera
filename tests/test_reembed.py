"""Embedding-scheme migration: legacy DBs are detected and rebuilt atomically."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import embed  # noqa: E402
import hera_db  # noqa: E402
import reembed  # noqa: E402


def _fake(text, **_):
    v = [0.0] * embed.DIM
    v[len(text) % embed.DIM] = 3.0  # deterministic, deliberately non-unit
    return v


def _legacy_db(tmp_path):
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    conn.execute("DELETE FROM config WHERE key='embed_scheme'")
    conn.execute("CREATE TABLE IF NOT EXISTS pages_fts_map (rowid INTEGER PRIMARY KEY, "
                 "page_id TEXT NOT NULL UNIQUE)")
    for i, title in enumerate(["Alpha", "Beta"]):
        pid = f"P{i}"
        conn.execute("INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at) "
                     "VALUES (?,?,'[]','concept','x','t','t')", (pid, title))
        cur = conn.execute("INSERT INTO pages_fts(title, body) VALUES (?, ?)", (title, "body " * i))
        conn.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)", (cur.lastrowid, pid))
        conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
                     (pid, embed.pack(_fake(title))))
    conn.commit()
    return conn


def test_fresh_db_is_born_on_current_scheme(tmp_path):
    assert hera_db.embed_scheme(hera_db.ensure_ready(tmp_path / "n.db")) == embed.SCHEME


def test_legacy_db_is_detected_and_rebuilt(tmp_path, monkeypatch):
    conn = _legacy_db(tmp_path)
    hera_db.init_schema(conn)  # re-init must NOT stamp a populated legacy index
    assert hera_db.embed_scheme(conn) is None
    monkeypatch.setattr(embed, "embed", _fake)
    assert reembed.reembed(conn) == 2
    assert hera_db.embed_scheme(conn) == embed.SCHEME
    import struct
    for (blob,) in conn.execute("SELECT embedding FROM pages_vec"):
        v = struct.unpack(f"{embed.DIM}f", blob)
        assert abs(sum(x * x for x in v) - 1.0) < 1e-5  # unit length now


def test_failure_midway_leaves_db_untouched(tmp_path, monkeypatch):
    conn = _legacy_db(tmp_path)
    before = conn.execute("SELECT page_id, embedding FROM pages_vec ORDER BY page_id").fetchall()
    calls = {"n": 0}

    def flaky(text, **_):
        calls["n"] += 1
        if calls["n"] == 2:
            raise embed.EmbedError("ollama died")
        return _fake(text)

    monkeypatch.setattr(embed, "embed", flaky)
    with pytest.raises(embed.EmbedError):
        reembed.reembed(conn)
    after = conn.execute("SELECT page_id, embedding FROM pages_vec ORDER BY page_id").fetchall()
    assert after == before and hera_db.embed_scheme(conn) is None


def test_query_and_document_vectors_are_unit_and_prefixed(monkeypatch):
    seen = []
    monkeypatch.setattr(embed, "embed", lambda t, **k: (seen.append(t), _fake(t))[1])
    q, d = embed.embed_query("x"), embed.embed_document("x")
    assert seen == ["search_query: x", "search_document: x"]
    for v in (q, d):
        assert abs(sum(a * a for a in v) - 1.0) < 1e-9
    assert abs(embed.cosine_from_distance(0.0) - 1.0) < 1e-12
