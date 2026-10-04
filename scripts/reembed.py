#!/usr/bin/env python3
"""reembed.py — rebuild every page vector under the current embedding scheme.

Needed once when a database was indexed under an older scheme (see
embed.SCHEME): legacy vectors were raw and unprefixed, so queries embedded the
current way would compare against incompatible vectors. The text embedded is
exactly what ingest indexed — title + the first 2000 chars of the body stored
in pages_fts — so nothing is read from wiki/ and nothing in wiki/ changes.

All vectors are computed before any row is written, then swapped in one
transaction, so an Ollama failure part-way leaves the database untouched.

Usage:
  python scripts/reembed.py [--db PATH] [--if-needed]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import embed  # noqa: E402
import hera_db  # noqa: E402


def reembed(conn) -> int:
    """Re-embed every indexed page. Returns the number of vectors written."""
    # Created lazily by ingest/search; same DDL here so an un-ingested DB works.
    conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid   INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id)
    )""")
    rows = conn.execute(
        "SELECT p.id, f.title, f.body FROM pages p "
        "JOIN pages_fts_map m ON m.page_id = p.id "
        "JOIN pages_fts f ON f.rowid = m.rowid").fetchall()
    vecs = [(pid, embed.pack(embed.embed_document(f"{title}\n{(body or '')[:2000]}")))
            for pid, title, body in rows]
    with conn:
        for pid, blob in vecs:
            conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (pid,))
            conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)", (pid, blob))
        conn.execute("INSERT INTO config(key, value) VALUES ('embed_scheme', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (embed.SCHEME,))
    return len(vecs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=pathlib.Path, default=hera_db.DB_PATH)
    ap.add_argument("--if-needed", action="store_true",
                    help="no-op when the database is already on the current scheme")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(f"reembed: no database at {a.db}")
        return 0
    conn = hera_db.ensure_ready(a.db)
    if a.if_needed and hera_db.embed_scheme(conn) == embed.SCHEME:
        print(f"reembed: {a.db} already on {embed.SCHEME}")
        return 0
    n = reembed(conn)
    print(f"reembed: {n} vector(s) rebuilt in {a.db} ({embed.SCHEME})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
