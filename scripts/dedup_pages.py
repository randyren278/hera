#!/usr/bin/env python3
"""Collapse duplicate `pages` rows that share a path down to one row each.

Background: a bug in ingest.py (fixed separately) minted a fresh ULID on every
re-ingest of an existing page, inserting a second `pages` row at the same path
(path has no UNIQUE constraint) plus duplicate FTS + vec index rows. This engine
cleans up the accumulated duplicates.

Policy:
  - Group live pages (archived_at IS NULL) by path; a path with >1 row has dupes.
  - Survivor = the OLDEST-created row (min created_at, ties broken by id) — it is
    the id existing wikilinks/citations most likely already reference.
  - For each loser row: re-point citations/conflicts FKs onto the survivor, then
    delete the loser's pages_fts row (via pages_fts_map rowid), its pages_fts_map
    row, its pages_vec row, and finally the pages row.
  - Idempotent: a second apply() finds zero duplicates.

All DB access goes through hera_db.connect() (loads sqlite-vec), per the vault
invariant against hand-editing hera.db. Dry-run is the default; --apply mutates.
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hera_db  # noqa: E402


def _duplicate_groups(conn):
    """Return {path: [ids...]} for every path with >1 live row, ids ordered
    oldest-first (survivor is [0])."""
    paths = [r[0] for r in conn.execute(
        "SELECT path FROM pages WHERE archived_at IS NULL "
        "GROUP BY path HAVING count(*) > 1"
    ).fetchall()]
    groups = {}
    for p in paths:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM pages WHERE path = ? AND archived_at IS NULL "
            "ORDER BY created_at ASC, id ASC", (p,)
        ).fetchall()]
        groups[p] = ids
    return groups


def dry_run(conn) -> int:
    groups = _duplicate_groups(conn)
    total_losers = sum(len(ids) - 1 for ids in groups.values())
    print(f"duplicate paths: {len(groups)}")
    print(f"rows to remove : {total_losers}")
    for path, ids in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  keep {ids[0]}  drop {len(ids)-1:>3}  {path}")
    if len(groups) > 20:
        print(f"  ... and {len(groups) - 20} more paths")
    return 0


def _delete_loser(conn, loser: str, survivor: str):
    # Re-point FK references so the loser can be deleted without orphaning them.
    conn.execute("UPDATE citations SET page_id = ? WHERE page_id = ?", (survivor, loser))
    conn.execute("UPDATE conflicts SET page_id = ? WHERE page_id = ?", (survivor, loser))
    conn.execute("UPDATE conflicts SET source_new_id = ? WHERE source_new_id = ?", (survivor, loser))
    # Delete the FTS row via its mapped rowid, then the map row.
    row = conn.execute("SELECT rowid FROM pages_fts_map WHERE page_id = ?", (loser,)).fetchone()
    if row:
        conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM pages_fts_map WHERE page_id = ?", (loser,))
    conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (loser,))
    conn.execute("DELETE FROM pages WHERE id = ?", (loser,))


def apply(conn) -> int:
    groups = _duplicate_groups(conn)
    removed = 0
    for path, ids in groups.items():
        survivor, losers = ids[0], ids[1:]
        for loser in losers:
            _delete_loser(conn, loser, survivor)
            removed += 1
    conn.commit()
    print(f"deduped {len(groups)} paths, removed {removed} rows")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    conn = hera_db.connect()
    try:
        if "--apply" in argv:
            return apply(conn)
        return dry_run(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
