#!/usr/bin/env python3
"""team_index.py — hybrid index for the team space, keyed by publisher ULID.

The team space is plain redacted Markdown, one folder per owner
(team-staging/<owner>/**). Each page already carries the publisher's
ULID in its `id:` frontmatter (stamped by publish.py). This engine builds a
SEPARATE SQLite index — team.db — using the SAME schema and retrieval
substrate as the personal hera.db (FTS5 BM25 + sqlite-vec 768-dim), so team
pages are sorted and retained identically to local pages.

team.db is deliberately a distinct file from hera.db: team pages must never
enter the personal index (isolation invariant). Retrieval fuses the two DBs at
query time; nothing here writes to hera.db.

CLI:
  team_index.py reindex [--all]   # reindex changed pages (or all with --all)
  team_index.py --doctor          # counts + integrity, exit 1 on mismatch
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[1]
STAGING = REPO / "team-staging"
TEAM_DB = pathlib.Path(os.environ.get("HERA_TEAM_DB", REPO / "team.db"))

sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import embed as _embed  # noqa: E402

# Files under staging that are not team pages.
_SKIP_NAMES = {"README.md"}


def open_team_db():
    """Connect to team.db with sqlite-vec loaded, ensure the canonical schema,
    then add the team-only page_meta table (owner/source/path/mtime). page_meta
    lives here — never in hera_db.SCHEMA — so team-only columns stay out of the
    personal DB."""
    conn = hera_db.connect(TEAM_DB)
    hera_db.init_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS page_meta (
        page_id  TEXT PRIMARY KEY,
        owner    TEXT NOT NULL,
        source   TEXT NOT NULL,
        rel_path TEXT NOT NULL,
        mtime    REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid   INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id)
    )""")
    return conn


def _no_team_space() -> bool:
    """No team space → no remote configured AND no staging clone on disk."""
    # Lazy import to avoid a hard dependency when team_sync isn't needed.
    try:
        import team_sync  # noqa: E402
        return team_sync._resolve_remote() is None and not (STAGING / ".git").exists()
    except Exception:
        return not STAGING.exists()


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (shallow frontmatter dict of string values, body)."""
    fm: dict[str, str] = {}
    if not text.startswith("---\n"):
        return fm, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return fm, text
    for line in text[4:end].splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, text[end + 5:]


def _owner_for(path: pathlib.Path, fm: dict) -> str:
    """Owner: prefer the `owner:` frontmatter field, else the first path segment
    under team-staging/."""
    owner = fm.get("owner")
    if owner:
        return owner
    try:
        rel = path.relative_to(STAGING).parts
        return rel[0] if rel else "team"
    except ValueError:
        return "team"


def _index_page(conn, path: pathlib.Path) -> str | None:
    """Upsert one team page's pages/fts/vec/meta rows. Returns the page_id, or
    None if the page has no parseable ULID (skipped)."""
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    pid = fm.get("id")
    if not pid:
        return None  # caller warns

    title = fm.get("title", path.stem)
    type_ = fm.get("type", "concept")
    aliases = fm.get("aliases", "[]")
    owner = _owner_for(path, fm)
    rel_path = path.relative_to(REPO).as_posix()
    mtime = path.stat().st_mtime
    now = hera_db.time.strftime("%Y-%m-%dT%H:%M:%S")

    # pages row (upsert by id).
    conn.execute(
        "INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, aliases=excluded.aliases, "
        "type=excluded.type, path=excluded.path, updated_at=excluded.updated_at",
        (pid, title, aliases, type_, rel_path, now, now),
    )

    # FTS row — same payload discipline as ingest.py:_index_page_search.
    row = conn.execute("SELECT rowid FROM pages_fts_map WHERE page_id = ?", (pid,)).fetchone()
    if row:
        rowid = row[0]
        conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (rowid,))
        conn.execute("INSERT INTO pages_fts(rowid, title, body) VALUES (?, ?, ?)",
                     (rowid, title, body))
    else:
        cur = conn.execute("INSERT INTO pages_fts(title, body) VALUES (?, ?)", (title, body))
        conn.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)",
                     (cur.lastrowid, pid))

    # Vector row — identical payload rule as local ingest (title + first ~2000 chars).
    payload = f"{title}\n{body[:2000]}"
    vec = _embed.embed(payload)
    conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (pid,))
    conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
                 (pid, _embed.pack(vec)))

    # Meta row (team-only).
    conn.execute(
        "INSERT INTO page_meta(page_id, owner, source, rel_path, mtime) "
        "VALUES (?, ?, 'team', ?, ?) "
        "ON CONFLICT(page_id) DO UPDATE SET owner=excluded.owner, "
        "rel_path=excluded.rel_path, mtime=excluded.mtime",
        (pid, owner, rel_path, mtime),
    )
    return pid


def _drop_page(conn, pid: str) -> None:
    """Remove all index rows for a page whose Markdown file has vanished."""
    row = conn.execute("SELECT rowid FROM pages_fts_map WHERE page_id = ?", (pid,)).fetchone()
    if row:
        conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM pages_fts_map WHERE page_id = ?", (pid,))
    conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (pid,))
    conn.execute("DELETE FROM page_meta WHERE page_id = ?", (pid,))
    conn.execute("DELETE FROM pages WHERE id = ?", (pid,))


def _staged_pages() -> list[pathlib.Path]:
    if not STAGING.exists():
        return []
    return [p for p in STAGING.rglob("*.md")
            if "/.git/" not in p.as_posix() and p.name not in _SKIP_NAMES]


def reindex(changed_only: bool = True) -> int:
    """(Re)build team.db from staged Markdown. Returns count of pages indexed.
    Clean no-op (returns 0) when no team space is configured."""
    if _no_team_space():
        return 0

    conn = open_team_db()
    files = _staged_pages()

    # Map of page_id → stored mtime for change detection.
    stored: dict[str, float] = {}
    live_ids_by_path: dict[str, str] = {}
    for pid, rp, mt in conn.execute("SELECT page_id, rel_path, mtime FROM page_meta").fetchall():
        stored[rp] = mt
        live_ids_by_path[rp] = pid

    seen_ids: set[str] = set()
    indexed = 0
    warnings: list[str] = []

    with conn:
        for f in files:
            rel = f.relative_to(REPO).as_posix()
            if changed_only and rel in stored and f.stat().st_mtime <= stored[rel]:
                # Unchanged — keep its id in seen set so it isn't pruned.
                seen_ids.add(live_ids_by_path[rel])
                continue
            pid = _index_page(conn, f)
            if pid is None:
                warnings.append(f"skipped (no id:): {rel}")
                continue
            seen_ids.add(pid)
            indexed += 1

        # Prune pages whose file disappeared.
        for pid, rp, _mt in conn.execute("SELECT page_id, rel_path, mtime FROM page_meta").fetchall():
            if pid not in seen_ids and not (REPO / rp).exists():
                _drop_page(conn, pid)

    for w in warnings:
        sys.stderr.write(w + "\n")
    return indexed


def doctor() -> int:
    conn = open_team_db()
    pages = conn.execute("SELECT count(*) FROM pages").fetchone()[0]
    fts = conn.execute("SELECT count(*) FROM pages_fts").fetchone()[0]
    vec = conn.execute("SELECT count(*) FROM pages_vec").fetchone()[0]
    meta = conn.execute("SELECT count(*) FROM page_meta").fetchone()[0]
    print(f"team.db: {TEAM_DB}")
    print(f"  pages={pages} fts={fts} vec={vec} meta={meta}")
    # Every page must have a vec row (no page_meta orphans, counts equal).
    missing_vec = conn.execute(
        "SELECT count(*) FROM pages p WHERE NOT EXISTS "
        "(SELECT 1 FROM pages_vec v WHERE v.page_id = p.id)").fetchone()[0]
    if missing_vec:
        print(f"  FAIL {missing_vec} pages without a vector row")
        return 1
    if not (pages == fts == vec == meta):
        print("  FAIL counts unequal")
        return 1
    print("  ok (counts equal, every page vectored)")
    return 0


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doctor", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p_re = sub.add_parser("reindex")
    p_re.add_argument("--all", action="store_true")
    a = ap.parse_args()

    if a.doctor:
        return doctor()
    if a.cmd == "reindex":
        n = reindex(changed_only=not a.all)
        print(f"indexed {n} team page(s)")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
