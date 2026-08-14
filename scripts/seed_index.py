"""seed_index.py — one-time seed-pack indexer (Phase 3).

Indexes a seed directory (e.g. tests/fixtures/seed-pack) into the personal
Hera index by
REUSING the ingest primitives — no LLM rewrite, personal-store only,
idempotent on the baked ULID.

Contract:
    seed_index <pack_dir> [--force]

For each .md under <pack_dir>/concepts/ and <pack_dir>/entities/:
  - Parse frontmatter: id (baked ULID — used verbatim, never regenerated),
    title, type, aliases, tags, pinned.
  - Copy the file into wiki/<type>s/<filename> through locks.lock() (the
    mandatory write path). The body is written verbatim — we do NOT route
    through ingest._write_page_file, which would rebuild frontmatter and drop
    tags/pinned.
  - Build an ingest.PageWrite with the baked id, call ingest._upsert_page then
    ingest._index_page_search (which keys pages_vec/pages_fts_map on page_id, so
    re-running with the same id overwrites in place — zero new rows).
  - After indexing, mark pinned=1 for pages whose frontmatter has tags: [seed]
    (or pinned: true).

Idempotency: a page whose id already has a pages_vec row is SKIPPED (no
re-embed) unless --force is passed. Either way row counts never grow on a
second run, because _index_page_search overwrites by page_id.

Writes ONLY the personal store (never the shared team store). Never invokes
an LLM / subprocess.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import ingest  # noqa: E402
import locks  # noqa: E402

WIKI = REPO / "wiki"

# Page type → its wiki subdirectory. Explicit (not naive `type_ + "s"`) because
# entity → entities, not "entitys". This governs BOTH the on-disk write path and
# the path stored in pages.path via PageWrite — they must agree.
TYPE_DIR = {
    "concept": "concepts",
    "entity": "entities",
    "source": "sources",
    "question": "questions",
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a page into (frontmatter dict, body). Minimal YAML — enough for
    the flat scalar / JSON-array frontmatter the seed pack carries."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_block = text[4:end]
    body = text[end + 5:]
    fm: dict = {}
    for line in fm_block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        fm[key] = _coerce(raw)
    return fm, body


def _coerce(raw: str):
    """Coerce a frontmatter scalar: JSON arrays, quoted strings, bools."""
    if raw == "":
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # e.g. tags: [seed] (unquoted) — parse the inner tokens.
            inner = raw[1:-1].strip()
            if not inner:
                return []
            return [t.strip().strip('"').strip("'") for t in inner.split(",")]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


def _is_seed(fm: dict) -> bool:
    tags = fm.get("tags") or []
    if isinstance(tags, list) and "seed" in tags:
        return True
    return bool(fm.get("pinned") is True)


def _has_vec_row(conn, page_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM pages_vec WHERE page_id = ?", (page_id,)
    ).fetchone() is not None


def seed_index(pack_dir: str, force: bool = False, conn=None) -> dict:
    """Index every concept/entity page under pack_dir into hera.db.

    Returns a summary dict: {indexed, skipped, pinned, pages[]}.
    """
    pack = pathlib.Path(pack_dir)
    if not pack.is_absolute():
        pack = (REPO / pack).resolve()
    if not pack.is_dir():
        raise SystemExit(f"seed_index: pack dir not found: {pack}")

    if conn is None:
        conn = hera_db.ensure_ready()

    indexed = skipped = pinned_count = 0
    seen: list[str] = []

    for sub in ("concepts", "entities"):
        src_dir = pack / sub
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.md")):
            text = src.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            page_id = fm.get("id")
            if not page_id:
                raise SystemExit(f"seed_index: no baked id in frontmatter: {src}")
            title = fm.get("title") or src.stem
            type_ = fm.get("type") or (sub[:-1])  # concepts -> concept
            aliases = fm.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []

            try:
                subdir = TYPE_DIR[type_]
            except KeyError:
                raise SystemExit(f"seed_index: unknown page type {type_!r} in {src}")
            dest = WIKI / subdir / src.name
            pw = ingest.PageWrite(
                id=page_id, title=title, type=type_, path=dest,
                body_md=body, aliases=aliases,
            )

            # Idempotency fast-path: if this id is already embedded and we are
            # not forcing, skip re-embedding entirely. Row counts are unchanged
            # either way (index keys on page_id), but this avoids a needless
            # Ollama round-trip on the happy re-run.
            already = _has_vec_row(conn, page_id)
            if already and not force:
                skipped += 1
                seen.append(page_id)
                continue

            # Write the file verbatim (frontmatter + body as shipped) through
            # the lock — do NOT use ingest._write_page_file, which rebuilds
            # frontmatter and would drop tags/pinned.
            dest.parent.mkdir(parents=True, exist_ok=True)
            with locks.lock(dest, page_id=page_id, conn=conn,
                            allow_delta=False) as acq:
                if acq is not None:
                    dest.write_text(text, encoding="utf-8")

            ingest._upsert_page(conn, pw)
            ingest._index_page_search(conn, pw)

            if _is_seed(fm):
                conn.execute("UPDATE pages SET pinned = 1 WHERE id = ?", (page_id,))
                pinned_count += 1

            indexed += 1
            seen.append(page_id)

    conn.commit()
    return {"indexed": indexed, "skipped": skipped, "pinned": pinned_count,
            "pages": seen}


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Index a seed pack into hera.db")
    ap.add_argument("pack_dir",
                    help="seed pack directory (e.g. tests/fixtures/seed-pack)")
    ap.add_argument("--force", action="store_true",
                    help="re-embed even pages already indexed")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    a = ap.parse_args()
    r = seed_index(a.pack_dir, force=a.force)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "pages"}, indent=2))
    else:
        print(f"seed_index: indexed={r['indexed']} skipped={r['skipped']} "
              f"pinned={r['pinned']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
