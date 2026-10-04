#!/usr/bin/env python3
"""reindex_orphans.py — list (default) or index wiki pages missing from hera.db.

An orphan is a page file under wiki/{sources,concepts,entities,questions}/
that no `pages` row points at, so search never sees it (left behind by an
ingest that failed part-way before ingest journaled its writes). `--apply`
indexes each orphan from its own frontmatter (id, title, type, aliases, trust)
using ingest's primitives — the Markdown is never modified. Deleting orphans
instead is a human decision; this tool never deletes.

Usage:
  python scripts/reindex_orphans.py            # list
  python scripts/reindex_orphans.py --apply    # index them
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hera_db  # noqa: E402
import ingest  # noqa: E402

SUBDIRS = ("sources", "concepts", "entities", "questions")


def orphans(conn) -> list[pathlib.Path]:
    indexed = {r[0] for r in conn.execute("SELECT path FROM pages")}
    return sorted(p for sub in SUBDIRS for p in (ingest.WIKI / sub).glob("*.md")
                  if p.relative_to(ingest.REPO).as_posix() not in indexed)


def _page(path: pathlib.Path) -> ingest.PageWrite | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return None
    end = text.index("\n---\n", 4)
    fm = yaml.safe_load(text[4:end]) or {}
    if not fm.get("id"):
        return None
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = json.loads(aliases) if aliases.startswith("[") else [aliases]
    return ingest.PageWrite(id=str(fm["id"]), title=str(fm.get("title") or path.stem),
                            type=str(fm.get("type") or path.parent.name.rstrip("s")),
                            path=path, body_md=text[end + 5:], aliases=list(aliases),
                            trust=str(fm.get("trust") or "self"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    conn = hera_db.ensure_ready()
    found = orphans(conn)
    if not found:
        print("no orphan pages")
        return 0
    for p in found:
        print(p.relative_to(ingest.REPO).as_posix())
    if not a.apply:
        print(f"{len(found)} orphan page(s); --apply indexes them (Markdown unchanged)")
        return 0
    done = 0
    with conn:
        for p in found:
            pw = _page(p)
            if pw is None:
                print(f"  skipped (no frontmatter id): {p.name}", file=sys.stderr)
                continue
            ingest._upsert_page(conn, pw)
            ingest._index_page_search(conn, pw)
            done += 1
    print(f"indexed {done} of {len(found)} orphan page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
