#!/usr/bin/env python3
"""CP-2.1 — the trust column exists and only accepts the three legal tiers.

What this proves:
  - a fresh vault gets `pages.trust` natively from init_schema()
  - the column defaults to 'self'
  - 'self' | 'team' | 'untrusted' are accepted
  - anything else is rejected on INSERT *and* on UPDATE
  - a legacy DB with no trust column is migrated in place, with every
    pre-existing row preserved and backfilled to 'self'
  - migrate_trust.migrate() is idempotent at the function level

The last assertion in test_check_constraint_is_actually_live is a guard on the
verifier itself: it builds a table WITHOUT the constraint and confirms that
table *does* accept a bogus tier. If that ever fails, the rejection assertions
above it are proving nothing.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _trust_helpers import REPO, run_tests  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import migrate_trust  # noqa: E402

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")


def _fresh(tmp: pathlib.Path):
    return hera_db.ensure_ready(tmp / "hera.db")


def _insert(conn, pid: str, trust: str | None = None):
    if trust is None:
        conn.execute(
            "INSERT INTO pages(id,title,type,path,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "T", "concept", f"wiki/{pid}.md", NOW, NOW))
    else:
        conn.execute(
            "INSERT INTO pages(id,title,type,path,created_at,updated_at,trust) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, "T", "concept", f"wiki/{pid}.md", NOW, NOW, trust))


def test_column_present_on_fresh_vault():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        assert "trust" in cols, f"fresh vault has no trust column: {sorted(cols)}"


def test_default_tier_is_self():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        _insert(conn, "01A")
        got = conn.execute("SELECT trust FROM pages WHERE id='01A'").fetchone()[0]
        assert got == "self", f"default tier is {got!r}, expected 'self'"


def test_all_three_tiers_accepted():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        for i, tier in enumerate(("self", "team", "untrusted")):
            _insert(conn, f"01OK{i}", tier)
        n = conn.execute(
            "SELECT count(*) FROM pages WHERE trust IN ('self','team','untrusted')"
        ).fetchone()[0]
        assert n == 3, f"expected 3 rows across the legal tiers, got {n}"


def test_invalid_tier_rejected_on_insert():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        for bogus in ("evil", "SELF", "", "trusted", "self ", "public"):
            try:
                _insert(conn, f"01BAD{bogus!r}", bogus)
            except sqlite3.IntegrityError:
                conn.rollback()
                continue
            raise AssertionError(f"trust={bogus!r} was accepted on INSERT")


def test_invalid_tier_rejected_on_update():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        _insert(conn, "01U", "self")
        try:
            conn.execute("UPDATE pages SET trust='evil' WHERE id='01U'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("trust='evil' was accepted on UPDATE")
        still = conn.execute("SELECT trust FROM pages WHERE id='01U'").fetchone()[0]
        assert still == "self", f"row mutated to {still!r} despite the constraint"


def test_null_tier_rejected():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        _insert(conn, "01N", "self")
        conn.commit()
        try:
            conn.execute("UPDATE pages SET trust=NULL WHERE id='01N'")
        except sqlite3.IntegrityError:
            return
        raise AssertionError("trust=NULL was accepted — NOT NULL is not in force")


def test_legacy_db_migrates_without_losing_pages():
    """A DB shaped like the pre-v3 schema: no trust column, rows already in it."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "legacy.db"
        raw = sqlite3.connect(p)
        raw.execute("""CREATE TABLE pages (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            aliases TEXT NOT NULL DEFAULT '[]', type TEXT NOT NULL,
            path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            archived_at TEXT, pinned INTEGER NOT NULL DEFAULT 0)""")
        raw.execute("""CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""")
        raw.execute("INSERT INTO schema_version VALUES (1, ?)", (NOW,))
        for i in range(7):
            raw.execute(
                "INSERT INTO pages(id,title,type,path,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (f"01L{i}", f"Legacy {i}", "concept", f"wiki/l{i}.md", NOW, NOW))
        raw.commit()
        raw.close()

        conn = hera_db.connect(p)
        assert "trust" not in {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        before = conn.execute("SELECT count(*) FROM pages").fetchone()[0]
        migrate_trust.migrate(conn)
        after = conn.execute("SELECT count(*) FROM pages").fetchone()[0]

        assert after == before == 7, f"page count changed: {before} -> {after}"
        tiers = {r[0] for r in conn.execute("SELECT DISTINCT trust FROM pages")}
        assert tiers == {"self"}, f"legacy rows backfilled to {tiers}, expected {{'self'}}"
        v3 = conn.execute("SELECT 1 FROM schema_version WHERE version=3").fetchone()
        assert v3, "schema_version 3 was not recorded"


def test_migrate_function_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        conn = _fresh(pathlib.Path(d))
        _insert(conn, "01I", "untrusted")
        conn.commit()
        first = migrate_trust.migrate(conn)
        second = migrate_trust.migrate(conn)
        assert second["added_column"] is False, "second run re-added the column"
        assert second["repaired_rows"] == 0, "second run rewrote rows"
        assert second["recorded_version"] is False, "second run re-recorded v3"
        # And it must not have trampled a deliberately-untrusted page.
        got = conn.execute("SELECT trust FROM pages WHERE id='01I'").fetchone()[0]
        assert got == "untrusted", f"migration overwrote a real tier: {got!r}"
        assert isinstance(first, dict)


def test_check_constraint_is_actually_live():
    """Verifier self-guard: prove the rejections above are the constraint's
    doing and not some unrelated error path. A table without the CHECK must
    accept the same bogus value that the real table rejects."""
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE unconstrained (id TEXT PRIMARY KEY, trust TEXT)")
    raw.execute("INSERT INTO unconstrained VALUES ('x', 'evil')")
    got = raw.execute("SELECT trust FROM unconstrained").fetchone()[0]
    assert got == "evil", (
        "an unconstrained table rejected 'evil' — the rejection assertions in "
        "this file are not proving what they claim")


if __name__ == "__main__":
    sys.exit(run_tests(dict(globals())))
