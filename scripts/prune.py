"""prune.py — /brain-prune engine (design §9.5, ADR-07).

Rank eligible pages by total citation points, drop the middle band, archive
the losers. Nothing is destroyed — archived pages stay under wiki/.archive/
and are restorable.

Eligibility (ADR-07):
  - type IN ('concept','entity','question')
  - age >= prune_min_age_days (config, default 30) — young pages haven't had
    time to earn citations and would be unfairly killed by raw-count ranking
  - not already archived

Prune band (config):
  - prune_pct_low, prune_pct_high (default 40, 70)
  - keep the top (working context) and the bottom (rarely-used-but-important);
    prune the grey zone in between.

Usage:
  python3 scripts/prune.py candidates            # print who would be pruned
  python3 scripts/prune.py candidates --json
  python3 scripts/prune.py apply --yes           # actually prune (no confirmation)
  python3 scripts/prune.py restore <page-id>     # un-archive a page
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402
import locks     # noqa: E402


ARCHIVE = REPO / "wiki" / ".archive"
LOG = REPO / "wiki" / "log.md"


@dataclass
class Candidate:
    page_id: str
    title: str
    type_: str
    path: str
    created_at: str
    total_points: int


def _config(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def eligible(conn: sqlite3.Connection) -> list[Candidate]:
    """Return eligible pages with their total citation points, sorted ASC by points."""
    min_age = int(_config(conn, "prune_min_age_days", "30"))
    cutoff_dt = time.time() - min_age * 86400
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cutoff_dt))

    rows = conn.execute("""
        SELECT p.id, p.title, p.type, p.path, p.created_at,
               COALESCE(SUM(c.points), 0) AS pts
          FROM pages p
     LEFT JOIN citations c ON c.page_id = p.id
         WHERE p.type IN ('concept', 'entity', 'question')
           AND p.archived_at IS NULL
           AND p.pinned = 0
           AND p.created_at <= ?
      GROUP BY p.id
      ORDER BY pts ASC, p.created_at ASC
    """, (cutoff,)).fetchall()

    return [Candidate(page_id=r[0], title=r[1], type_=r[2], path=r[3],
                      created_at=r[4], total_points=int(r[5])) for r in rows]


def middle_band(cands: list[Candidate], low: int, high: int) -> list[Candidate]:
    """Return candidates whose citation-point rank sits between low and high percentile."""
    if len(cands) < 3:
        return []
    n = len(cands)
    lo = int(round(n * (low / 100.0)))
    hi = int(round(n * (high / 100.0)))
    lo = max(0, min(lo, n - 1))
    hi = max(lo, min(hi, n))
    return cands[lo:hi]


def prune(conn: sqlite3.Connection, cands: list[Candidate], dry_run: bool = True) -> dict:
    """Archive each candidate. Returns a summary. Dry-run prints intended actions
    but makes no changes."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    actions: list[dict] = []

    for c in cands:
        src = REPO / c.path
        dst = ARCHIVE / f"{c.page_id}.{pathlib.Path(c.path).name}"
        actions.append({
            "page_id": c.page_id, "title": c.title, "type": c.type_,
            "path": c.path, "archive": dst.relative_to(REPO).as_posix(),
            "total_points": c.total_points,
        })
        if dry_run:
            continue

        # Move file (through locking to be safe if any other writer is around).
        if src.exists():
            with locks.lock(src, page_id=c.page_id, conn=conn, allow_delta=False):
                shutil.move(str(src), str(dst))

        # Clear FTS + vec rows; keep pages row (with archived_at).
        conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (c.page_id,))
        row = conn.execute("SELECT rowid FROM pages_fts_map WHERE page_id = ?",
                           (c.page_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (row[0],))
            conn.execute("DELETE FROM pages_fts_map WHERE page_id = ?", (c.page_id,))
        conn.execute("UPDATE pages SET archived_at = ? WHERE id = ?", (now, c.page_id))
        conn.commit()

    # Log to wiki/log.md unless dry_run.
    if not dry_run and cands:
        entry = [f"## [{time.strftime('%Y-%m-%d')}] prune"]
        for c in cands:
            entry.append(f"- Archived [[{c.title}]] (id={c.page_id}, "
                         f"type={c.type_}, points={c.total_points})")
        entry.append("")
        if LOG.exists():
            old = LOG.read_text(encoding="utf-8")
            head_end = old.find("\n# Log\n")
            if head_end != -1:
                split = head_end + len("\n# Log\n\n")
                LOG.write_text(old[:split] + "\n".join(entry) + "\n" + old[split:],
                               encoding="utf-8")
            else:
                LOG.write_text(old + "\n".join(entry) + "\n", encoding="utf-8")
    return {"count": len(actions), "actions": actions, "dry_run": dry_run}


def restore(conn: sqlite3.Connection, page_id: str) -> str:
    """Un-archive a page: move the file back, clear archived_at.
    The FTS + vec rows are NOT restored automatically; the caller should re-ingest
    or call ingest._index_page_search to rebuild them."""
    row = conn.execute("SELECT title, path FROM pages WHERE id = ? AND archived_at IS NOT NULL",
                       (page_id,)).fetchone()
    if not row:
        raise SystemExit(f"page {page_id} not found in archive")
    title, orig_path = row
    src = ARCHIVE / f"{page_id}.{pathlib.Path(orig_path).name}"
    if not src.exists():
        raise SystemExit(f"archive file missing: {src}")
    dst = REPO / orig_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    conn.execute("UPDATE pages SET archived_at = NULL WHERE id = ?", (page_id,))
    conn.commit()

    # Rebuild search index for the restored page.
    import ingest
    body = dst.read_text(encoding="utf-8")
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    pw = ingest.PageWrite(id=page_id, title=title, type="concept",
                          path=dst, body_md=body)
    ingest._index_page_search(conn, pw)
    conn.commit()
    return dst.as_posix()


def _cli() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("candidates")
    p1.add_argument("--json", action="store_true")

    p2 = sub.add_parser("apply")
    p2.add_argument("--yes", action="store_true", help="skip confirmation")

    p3 = sub.add_parser("restore")
    p3.add_argument("page_id")

    a = ap.parse_args()
    conn = brain_db.connect()

    if a.cmd == "candidates":
        cs = eligible(conn)
        low = int(_config(conn, "prune_pct_low", "40"))
        high = int(_config(conn, "prune_pct_high", "70"))
        band = middle_band(cs, low, high)
        if a.json:
            print(json.dumps([c.__dict__ for c in band], indent=2))
        else:
            print(f"eligible: {len(cs)} pages · prune band {low}%-{high}% → {len(band)} candidates")
            for c in band:
                print(f"  [{c.total_points:>4} pts] {c.type_:<8} {c.title}  ({c.path})")
        return 0

    if a.cmd == "apply":
        cs = eligible(conn)
        low = int(_config(conn, "prune_pct_low", "40"))
        high = int(_config(conn, "prune_pct_high", "70"))
        band = middle_band(cs, low, high)
        if not band:
            print("nothing to prune"); return 0
        if not a.yes:
            print("Confirm pruning these pages (y/N):")
            for c in band:
                print(f"  [{c.total_points:>4} pts] {c.title}")
            reply = input("> ").strip().lower()
            if reply not in ("y", "yes"):
                print("aborted"); return 1
        summary = prune(conn, band, dry_run=False)
        print(json.dumps(summary, indent=2))
        return 0

    if a.cmd == "restore":
        path = restore(conn, a.page_id)
        print(f"restored to {path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
