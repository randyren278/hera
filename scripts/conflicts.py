"""conflicts.py — conflict-queue CLI and resolution primitives (design §7.4).

Used by:
  - .claude/skills/hera-conflicts/SKILL.md (interactive resolution)
  - scripts/e2e_conflicts.sh (deterministic resolution in tests)

Actions per open conflict:
  - resolved_new    → new claim wins. Update page body, append `## Superseded`
                      with the old claim + source wikilink + date range.
  - resolved_old    → old claim wins. Just mark resolved.
  - resolved_both   → both true in different contexts. Append a permanent
                      `> [!conflict]` callout on the page with both claims.
                      (Design §7.4 — this is the ONLY situation such a callout
                      exists; it is never a pending-state artifact.)
  - dismissed       → not a real conflict. Mark resolved with reason.

Writes go through scripts/locks.py.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import locks     # noqa: E402


WIKI = REPO / "wiki"


def _load_conflict(conn, cid: int) -> dict:
    row = conn.execute(
        "SELECT c.id, c.page_id, c.claim_old, c.claim_new, c.source_new_id, c.status, p.title, p.path "
        "FROM conflicts c JOIN pages p ON p.id = c.page_id WHERE c.id = ?",
        (cid,),
    ).fetchone()
    if not row:
        raise SystemExit(f"conflict #{cid} not found")
    keys = ("id", "page_id", "claim_old", "claim_new", "source_new_id",
            "status", "page_title", "page_path")
    return dict(zip(keys, row))


def _load_open(conn, cid: int) -> dict:
    """The conflict, if still open. Resolving twice would append a second
    Update/Superseded section (or callout) to the page."""
    c = _load_conflict(conn, cid)
    if c["status"] != "open":
        raise SystemExit(f"conflict #{cid} is already {c['status']} — nothing to do")
    return c


def _reindex(conn, c: dict, page_path: pathlib.Path) -> None:
    """Refresh the page's FTS + vector rows after its body changed, so search
    stops matching the superseded text. Best effort: an embed failure leaves
    the resolution in place and says so."""
    import ingest  # lazy: ingest imports this module during auto-resolve
    _fm, body = _split_frontmatter(page_path.read_text(encoding="utf-8"))
    try:
        ingest._index_page_search(conn, ingest.PageWrite(
            id=c["page_id"], title=c["page_title"], type="concept",
            path=page_path, body_md=body))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        print(f"warning: conflict resolved but page not reindexed ({e}); "
              "it is reindexed on its next ingest", file=sys.stderr)


def _mark_resolved(conn, cid: int, status: str) -> None:
    conn.execute(
        "UPDATE conflicts SET status = ?, resolved_at = ? WHERE id = ?",
        (status, time.strftime("%Y-%m-%dT%H:%M:%S"), cid),
    )
    conn.commit()


def _source_wikilink(conn, source_id: str | None) -> str:
    if not source_id:
        return "(unknown source)"
    row = conn.execute("SELECT title FROM pages WHERE id = ?", (source_id,)).fetchone()
    return f"[[{row[0]}]]" if row else "(unknown source)"


def resolve_new(conn, cid: int, new_body_replacement: str | None = None) -> None:
    """New claim wins. Replace the page body (or use new_body_replacement if
    provided by the caller) and append an `## Superseded` section preserving
    the old claim + source + date. Nothing is destroyed."""
    c = _load_open(conn, cid)
    page_path = REPO / c["page_path"]
    if not page_path.exists():
        raise SystemExit(f"page file missing: {page_path}")
    text = page_path.read_text(encoding="utf-8")

    src_link = _source_wikilink(conn, c["source_new_id"])
    date = time.strftime("%Y-%m-%d")

    # Split frontmatter/body
    fm, body = _split_frontmatter(text)

    # Body update: append "Update:" paragraph with the new claim, then
    # ## Superseded with the old claim.
    body = body.rstrip() + "\n\n"
    body += f"## Update ({date})\n\n{c['claim_new']}  (source: {src_link})\n\n"
    body += "## Superseded\n\n"
    body += f"- **{c['claim_old']}**  (believed until {date}; contradicted by {src_link})\n"

    new_text = fm + body

    with locks.lock(page_path, page_id=c["page_id"], conn=conn, allow_delta=False):
        page_path.write_text(new_text, encoding="utf-8")

    _mark_resolved(conn, cid, "resolved_new")
    _reindex(conn, c, page_path)


def resolve_old(conn, cid: int) -> None:
    _load_open(conn, cid)
    _mark_resolved(conn, cid, "resolved_old")


def resolve_both(conn, cid: int) -> None:
    """Both true in different contexts. Append a permanent `> [!conflict]`
    callout — the ONLY place this callout exists (design §7.4)."""
    c = _load_open(conn, cid)
    page_path = REPO / c["page_path"]
    if not page_path.exists():
        raise SystemExit(f"page file missing: {page_path}")
    text = page_path.read_text(encoding="utf-8")
    src_link = _source_wikilink(conn, c["source_new_id"])
    fm, body = _split_frontmatter(text)
    body = body.rstrip() + "\n\n"
    body += "> [!conflict] Two claims, both legitimate in different contexts\n"
    body += f"> - {c['claim_old']}\n"
    body += f"> - {c['claim_new']}  (source: {src_link})\n"
    new_text = fm + body
    with locks.lock(page_path, page_id=c["page_id"], conn=conn, allow_delta=False):
        page_path.write_text(new_text, encoding="utf-8")
    _mark_resolved(conn, cid, "resolved_both")
    _reindex(conn, c, page_path)


def dismiss(conn, cid: int) -> None:
    _load_open(conn, cid)
    _mark_resolved(conn, cid, "dismissed")


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[: end + 5], text[end + 5:]
    return "", text


def list_open(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.claim_old, c.claim_new, c.origin_cwd, c.detected_at, p.title "
        "FROM conflicts c JOIN pages p ON p.id = c.page_id "
        "WHERE c.status = 'open' ORDER BY c.detected_at"
    ).fetchall()
    keys = ("id", "claim_old", "claim_new", "origin_cwd", "detected_at", "page_title")
    return [dict(zip(keys, r)) for r in rows]


def _cli() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for act in ("new", "old", "both", "dismiss"):
        p = sub.add_parser(act)
        p.add_argument("cid", type=int)
    a = ap.parse_args()

    conn = hera_db.connect()
    if a.cmd == "list":
        for c in list_open(conn):
            print(f"[{c['id']}] {c['page_title']}")
            print(f"   old: {c['claim_old']}")
            print(f"   new: {c['claim_new']}")
            print(f"   origin={c['origin_cwd']} at {c['detected_at']}")
        return 0
    if a.cmd == "new":     resolve_new(conn, a.cid);  return 0
    if a.cmd == "old":     resolve_old(conn, a.cid);  return 0
    if a.cmd == "both":    resolve_both(conn, a.cid); return 0
    if a.cmd == "dismiss": dismiss(conn, a.cid);      return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
