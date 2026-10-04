#!/usr/bin/env python3
"""migrate_trust.py — add and backfill pages.trust (design §5.1, schema v3).

Idempotent by construction: every step is a no-op when already applied, so
running this twice in a row exits 0 both times.

  1. ALTER TABLE pages ADD COLUMN trust TEXT NOT NULL DEFAULT 'self'
     CHECK (trust IN ('self','team','untrusted'))   — only when absent.
     The NOT NULL DEFAULT backfills every pre-existing row to 'self' as part
     of the ALTER, which is correct: everything already in a vault was written
     by the operator.
  2. Repair any row left with NULL/'' trust (only reachable if an older build
     added the column without the default).
  3. Record schema_version 3.

Team pages are NOT backfilled here. They are indexed exclusively in team.db
(ADR-14); hera.db never holds a teammate's page, so there is nothing in this
database to mark 'team'. Run with --report to see the resulting tier census.

Usage:
  python scripts/migrate_trust.py [--report]
"""
from __future__ import annotations

import argparse
import sys
import time

import hera_db


def migrate(conn) -> dict:
    """Apply the trust migration. Returns a summary dict. Safe to re-run."""
    actions = {"added_column": False, "repaired_rows": 0, "recorded_version": False}
    with conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        if "trust" not in cols:
            conn.execute("ALTER TABLE pages ADD COLUMN trust TEXT NOT NULL "
                         "DEFAULT 'self' CHECK (trust IN ('self','team','untrusted'))")
            actions["added_column"] = True

        cur = conn.execute(
            "UPDATE pages SET trust = 'self' WHERE trust IS NULL OR trust = ''")
        actions["repaired_rows"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        if not conn.execute("SELECT 1 FROM schema_version WHERE version = 3").fetchone():
            conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (3, ?)",
                         (time.strftime("%Y-%m-%dT%H:%M:%S"),))
            actions["recorded_version"] = True
    return actions


def census(conn) -> list[tuple[str, int]]:
    return list(conn.execute(
        "SELECT trust, count(*) FROM pages GROUP BY trust ORDER BY trust"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="print the trust-tier census and exit without migrating")
    a = ap.parse_args()

    # Record the pre-state before ensure_ready(), because init_schema() applies
    # the same v3 step — without this peek we could not tell a genuine first
    # migration from a re-run, and would report "no-op" for both.
    probe = hera_db.connect()
    had_column = "trust" in {r[1] for r in probe.execute("PRAGMA table_info(pages)")}
    probe.close()

    # ensure_ready() guarantees the tables exist before we touch them. On a
    # current vault the migrate() call below is therefore already a no-op; on a
    # legacy DB either path lands the column. The verification after migrate()
    # is the real contract.
    conn = hera_db.ensure_ready()
    if a.report:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        if "trust" not in cols:
            print("migrate_trust: column absent — migration not applied")
            return 1
        for tier, n in census(conn):
            print(f"  {tier}: {n}")
        return 0

    total_before = conn.execute("SELECT count(*) FROM pages").fetchone()[0]
    actions = migrate(conn)
    total_after = conn.execute("SELECT count(*) FROM pages").fetchone()[0]

    # A migration that loses a page is a data-loss bug, not a migration.
    if total_after != total_before:
        print(f"migrate_trust: FAIL — page count changed "
              f"{total_before} -> {total_after}", file=sys.stderr)
        return 1

    nulls = conn.execute(
        "SELECT count(*) FROM pages WHERE trust IS NULL OR trust = ''").fetchone()[0]
    if nulls:
        print(f"migrate_trust: FAIL — {nulls} pages still missing trust", file=sys.stderr)
        return 1

    changed = (not had_column or actions["added_column"]
               or actions["recorded_version"] or actions["repaired_rows"])
    state = "applied" if changed else "already applied (no-op)"
    print(f"migrate_trust: {state}; pages={total_after}")
    for tier, n in census(conn):
        print(f"  {tier}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
