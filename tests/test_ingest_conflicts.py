"""Behaviour tests for the ingest contradiction / conflict-enqueue path.

These drive the REAL ingest_source() control flow with only the
non-deterministic edges stubbed (the extraction LLM, the contradiction
detector, the explicit-statement check, and the Ollama embedder). Everything
that matters for the two bugs under test — the per-page contradiction loop,
_enqueue_conflict, conflicts.resolve_new, and the deferred _upsert_page batch —
runs for real against a throwaway vault + brain.db.

Two invariants are pinned, each guarding a real bug in scripts/ingest.py:

  1. FK safety of conflict enqueue. `conflicts.source_new_id REFERENCES
     pages(id)`. ingest_source enqueues a conflict naming the new source page,
     so that source page's row must exist in `pages` before the enqueue. The
     reported crash is `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
     because the source page is upserted only AFTER the conflict loop.
     => test_ingest_with_contradiction_does_not_crash_on_fk

  2. One `pages` row per file path across an auto-resolve. On the ADR-11
     session auto-resolve branch the existing page is resolved in place; no
     second `pages` row may be minted for its path.
     => test_ingest_autoresolve_leaves_one_page_row_per_path

Both tests FAIL on the current (buggy) code and PASS once the bugs are fixed —
they are regression guards, not characterization snapshots.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402
import ingest  # noqa: E402
import conflicts  # noqa: E402


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Throwaway vault: fresh brain.db + wiki/ tree, every vault-touching
    global repointed at tmp_path, and the four non-deterministic edges stubbed
    (extractor, embedder) so ingest_source runs deterministically offline."""
    db = tmp_path / "brain.db"
    wiki = tmp_path / "wiki"
    monkeypatch.setattr(brain_db, "DB_PATH", db)
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest, "WIKI", wiki)
    monkeypatch.setattr(conflicts, "REPO", tmp_path)
    for sub in ("concepts", "entities", "sources", ".raw/articles"):
        (wiki / sub).mkdir(parents=True, exist_ok=True)
    # Deterministic 768-dim embedding — never touch Ollama.
    monkeypatch.setattr(ingest._embed, "embed", lambda text: [0.0] * 768)
    conn = brain_db.ensure_ready(db)
    yield conn, tmp_path, monkeypatch
    conn.close()


def _seed_concept(conn, root, page_id, title, body="Interns start on Monday."):
    """Insert an existing concept page (row + file) that a later ingest will
    contradict. The path MUST equal what ingest computes for this title
    (WIKI/concepts/<slugify(title)>.md), or _existing_page_at won't match and
    the contradiction branch never fires."""
    rel = f"wiki/concepts/{ingest._slugify(title)}.md"
    conn.execute(
        "INSERT INTO pages(id, type, title, path, created_at, updated_at) "
        "VALUES (?, 'concept', ?, ?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (page_id, title, rel),
    )
    conn.commit()
    f = root / rel
    f.write_text(
        f"---\nid: {page_id}\ntitle: \"{title}\"\ntype: concept\n---\n"
        f"> [!info] onboarding date\n\n{body}\n",
        encoding="utf-8",
    )


def _stub_extract_one_concept(monkeypatch, title, one_line, body):
    """Make the extractor return a source + a single concept whose title
    collides with an existing page (so the contradiction loop fires)."""
    payload = {
        "source": {"title": "Onboarding Agent Brief", "one_line": "a brief",
                   "key_takeaways": ["k1"], "body": "summary"},
        "concepts": [{"title": title, "one_line": one_line, "body": body, "aliases": []}],
        "entities": [],
        "warnings": [],
    }
    monkeypatch.setattr(ingest, "_call_claude_extract", lambda raw: payload)


def _write_source_file(root):
    src = root / "wiki" / ".raw" / "articles" / "brief.txt"
    src.write_text("Interns now start on Tuesday, not Monday.", encoding="utf-8")
    return src


# ---- Bug 2 (FK crash): ingest with a contradiction must not crash ---------

def test_ingest_with_contradiction_does_not_crash_on_fk(vault):
    """A non-session ingest whose concept contradicts an existing page enqueues
    a conflict for review. That enqueue names the new source page as
    source_new_id; ingest must have the source page in `pages` first, or the FK
    fails. Currently ingest_source enqueues before upserting the source ->
    IntegrityError. This test drives the real path and asserts it completes and
    records the open conflict."""
    conn, root, mp = vault
    _seed_concept(conn, root, "01OLD00000000000000000000", "Onboard")
    _stub_extract_one_concept(
        mp, title="Onboard", one_line="onboarding date",
        body="Interns start on Tuesday.")
    # Non-session => the contradiction is enqueued (not auto-resolved).
    mp.setattr(ingest, "_detect_contradiction",
               lambda old, new: {"verdict": "contradiction",
                                 "claim_old": "start Monday",
                                 "claim_new": "start Tuesday",
                                 "reason": "date changed"})
    src = _write_source_file(root)

    # Must not raise sqlite3.IntegrityError.
    ingest.ingest_source(str(src), source_kind="file", conn=conn)

    open_conflicts = conn.execute(
        "SELECT count(*) FROM conflicts WHERE page_id=? AND status='open'",
        ("01OLD00000000000000000000",),
    ).fetchone()[0]
    assert open_conflicts == 1
    # The source page named by the conflict must actually exist (FK satisfiable).
    src_new = conn.execute(
        "SELECT source_new_id FROM conflicts WHERE page_id=?",
        ("01OLD00000000000000000000",),
    ).fetchone()[0]
    assert conn.execute(
        "SELECT 1 FROM pages WHERE id=?", (src_new,)).fetchone() is not None


# ---- Bug 1 (duplicate row): ADR-11 auto-resolve keeps one row per path -----

def test_ingest_autoresolve_leaves_one_page_row_per_path(vault):
    """On a session ingest where the user explicitly restated the contradicted
    claim, ADR-11 auto-resolves in place. The existing page's path must still
    map to exactly one `pages` row afterwards — the auto-resolve branch must
    freeze old_id so the deferred _upsert_page batch does not mint a second row
    (fresh ULID, same path)."""
    conn, root, mp = vault
    _seed_concept(conn, root, "01OLD00000000000000000000", "Onboard")
    _stub_extract_one_concept(
        mp, title="Onboard", one_line="onboarding date",
        body="Interns start on Tuesday.")
    mp.setattr(ingest, "_detect_contradiction",
               lambda old, new: {"verdict": "contradiction",
                                 "claim_old": "start Monday",
                                 "claim_new": "start Tuesday",
                                 "reason": "date changed"})
    # Session + explicit user statement => ADR-11 auto-resolve branch.
    mp.setattr(ingest, "_user_stated_explicitly",
               lambda raw, claim: (True, "the user said Tuesday"))
    src = _write_source_file(root)

    ingest.ingest_source(str(src), source_kind="session", conn=conn)

    (n,) = conn.execute(
        "SELECT count(*) FROM pages WHERE path=?", ("wiki/concepts/Onboard.md",),
    ).fetchone()
    assert n == 1, f"expected exactly one pages row for the concept path, found {n}"
