"""Shared scaffolding for the trust-tier tests (design §5.1, phase P2).

Two things every trust test needs and neither pytest nor the checkpoint runner
provides:

  1. A **scratch vault** — its own hera.db and wiki/ under a temp dir — so no
     test ever writes to the operator's real 53-page vault.
  2. A **standalone runner** — the CP-2.x checks invoke these files as
     `.venv/bin/python tests/test_x.py`, not under pytest, so each file needs a
     main() that runs its test_* functions and exits non-zero on the first
     failure. The files stay pytest-collectable as well.

Deliberately NOT stubbed: embeddings. These tests call the real Ollama
endpoint, because the thing under test is whether an untrusted page survives
real BM25 + real dense retrieval + real RRF fusion. A stubbed embedder would
let a filter bug through.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import traceback

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import embed as _embed  # noqa: E402
import hera_db  # noqa: E402


class Vault:
    """A throwaway vault: temp hera.db + temp wiki/, wired so `hera_db.DB_PATH`
    and the inject hook's REPO both resolve here instead of the real vault."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.db_path = root / "hera.db"
        self.wiki = root / "wiki"
        self.wiki.mkdir(parents=True, exist_ok=True)
        # The inject hook resolves its imports as $HERA_VAULT/scripts. Point a
        # symlink at the real engines so a subprocess run of the hook against
        # this scratch vault exercises the real search path — with HERA_DB
        # aimed here, nothing it does can touch the operator's vault.
        link = root / "scripts"
        if not link.exists():
            link.symlink_to(REPO / "scripts", target_is_directory=True)
        self.conn = hera_db.ensure_ready(self.db_path)

    def add_page(self, title: str, body: str, trust: str = "self",
                 type_: str = "concept", archived: bool = False) -> str:
        """Create a real page: markdown file on disk + pages row + FTS row +
        vec row. Returns the page id. Mirrors ingest.py's indexing exactly, so
        a page added here is indistinguishable from an ingested one."""
        pid = f"01TEST{abs(hash(title)) % 10**12:012d}"
        rel = f"wiki/concepts/{title}.md"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(
            f"---\nid: {pid}\ntype: {type_}\ntitle: \"{title}\"\n"
            f"trust: {trust}\n---\n\n{body}\n", encoding="utf-8")

        self.conn.execute(
            "INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at,"
            "archived_at,trust) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET trust=excluded.trust",
            (pid, title, json.dumps([]), type_, rel, now, now,
             now if archived else None, trust))

        self.conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
            rowid   INTEGER PRIMARY KEY,
            page_id TEXT NOT NULL UNIQUE REFERENCES pages(id))""")
        cur = self.conn.execute(
            "INSERT INTO pages_fts(title, body) VALUES (?, ?)", (title, body))
        self.conn.execute(
            "INSERT OR REPLACE INTO pages_fts_map(rowid, page_id) VALUES (?, ?)",
            (cur.lastrowid, pid))

        vec = _embed.embed_document(f"{title}\n{body[:2000]}")
        self.conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (pid,))
        self.conn.execute(
            "INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
            (pid, _embed.pack(vec)))
        self.conn.commit()
        return pid


def run_tests(namespace: dict) -> int:
    """Run every test_* callable in `namespace`. Returns a process exit code.

    Each test takes no arguments and raises on failure. Prints one line per
    test so the checkpoint report carries real evidence, not just an exit code.
    """
    tests = [(n, f) for n, f in sorted(namespace.items())
             if n.startswith("test_") and callable(f)]
    if not tests:
        print("no tests found — a test file with no tests is a broken verifier")
        return 1
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0
