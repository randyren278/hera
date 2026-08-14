#!/usr/bin/env python3
"""hera_db.py — SQLite access layer for the Hera.

Owns:
  - connection factory that loads sqlite-vec
  - migration/creation of every table in design §5
  - --doctor: DB integrity, Ollama reachability, stale-lock scan,
    unmerged-delta count, scorer-log freshness, hook registration

Design invariants:
  - one file, hera.db in repo root (path overridable via HERA_DB env)
  - sqlite-vec loaded on every connection (search paths need it)
  - foreign keys ON, WAL journaling (safe for concurrent readers)
  - schema evolution: idempotent CREATE IF NOT EXISTS; a schema_version
    table records what migrations have applied
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

import sqlite_vec

REPO = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = pathlib.Path(os.environ.get("HERA_DB", REPO / "hera.db"))
SCORER_LOG = pathlib.Path(os.environ.get("HERA_SCORER_LOG", REPO / ".hera" / "scorer.log"))
WIKI = REPO / "wiki"
SETTINGS = REPO / ".claude" / "settings.json"

SCHEMA = [
    # Canonical page registry (title→ID resolution lives here)
    """CREATE TABLE IF NOT EXISTS pages (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        aliases     TEXT NOT NULL DEFAULT '[]',
        type        TEXT NOT NULL,
        path        TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        archived_at TEXT,
        pinned      INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pages_title ON pages(title)",

    # Citation scoreboard (ADR-03) — two tiers via `tier`.
    """CREATE TABLE IF NOT EXISTS citations (
        id         INTEGER PRIMARY KEY,
        page_id    TEXT NOT NULL REFERENCES pages(id),
        session_id TEXT NOT NULL,
        tier       TEXT NOT NULL CHECK (tier IN ('thinking','final')),
        points     INTEGER NOT NULL,
        cited_at   TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_citations_page ON citations(page_id)",

    # Full-text (BM25). Content-owning (not external-content): ingest inserts
    # rows keyed by rowid via pages_fts_map (populated in ingest.py).
    "CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(title, body)",

    # Vector index (sqlite-vec) — one 768-dim embedding per page.
    "CREATE VIRTUAL TABLE IF NOT EXISTS pages_vec USING vec0(page_id TEXT PRIMARY KEY, embedding FLOAT[768])",

    # Conflict queue (§7). Frozen-pending model.
    """CREATE TABLE IF NOT EXISTS conflicts (
        id             INTEGER PRIMARY KEY,
        page_id        TEXT NOT NULL REFERENCES pages(id),
        claim_old      TEXT NOT NULL,
        claim_new      TEXT NOT NULL,
        source_old_id  TEXT REFERENCES pages(id),
        source_new_id  TEXT REFERENCES pages(id),
        origin_cwd     TEXT,
        detected_at    TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open','resolved_new','resolved_old','resolved_both','dismissed')),
        resolved_at    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_open ON conflicts(status) WHERE status = 'open'",

    # Delta overflow registry (§6): pending writes that lost a lock race.
    """CREATE TABLE IF NOT EXISTS pending_deltas (
        id         INTEGER PRIMARY KEY,
        page_id    TEXT NOT NULL REFERENCES pages(id),
        delta_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        merged_at  TEXT
    )""",

    # Session parse cursor for the async scorer (§8.3).
    """CREATE TABLE IF NOT EXISTS session_cursors (
        session_id TEXT PRIMARY KEY,
        cursor     INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )""",

    # Filing idempotency for SessionEnd hook (§8.4).
    """CREATE TABLE IF NOT EXISTS filed_sessions (
        session_id TEXT PRIMARY KEY,
        filed_at   TEXT NOT NULL
    )""",

    # k/v config: scoring points, prune bands, relevance floor, etc.
    """CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",

    # Schema version bookkeeping.
    """CREATE TABLE IF NOT EXISTS schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )""",
]

DEFAULT_CONFIG = {
    "points_thinking": "1",
    "points_final": "5",
    "prune_pct_low": "40",
    "prune_pct_high": "70",
    "prune_min_age_days": "30",
    "inject_relevance_floor": "0.015",
    "inject_top_n": "3",
    "lock_retries": "3",
    "lock_backoff_seconds": "1.6",
    "stale_lock_seconds": "600",
}


def connect(db_path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    with conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        # Record schema v1 exactly once.
        row = conn.execute("SELECT 1 FROM schema_version WHERE version = 1").fetchone()
        if not row:
            conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (1, ?)",
                         (time.strftime("%Y-%m-%dT%H:%M:%S"),))
        # Migration v2: add pages.pinned to existing DBs. Idempotent — the
        # ALTER only runs when the column is absent (SQLite errors on a
        # duplicate ADD COLUMN), and the version row is recorded once.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        if "pinned" not in cols:
            conn.execute("ALTER TABLE pages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        row = conn.execute("SELECT 1 FROM schema_version WHERE version = 2").fetchone()
        if not row:
            conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (2, ?)",
                         (time.strftime("%Y-%m-%dT%H:%M:%S"),))
        # Seed defaults for any missing config keys — never overwrite.
        for k, v in DEFAULT_CONFIG.items():
            conn.execute("INSERT OR IGNORE INTO config(key, value) VALUES (?, ?)", (k, v))


def ensure_ready(db_path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    """Convenience: connect and initialise schema in one call."""
    conn = connect(db_path)
    init_schema(conn)
    return conn


# ----- doctor -----

def _ok(msg: str) -> None: print(f"  ok    {msg}")
def _warn(msg: str) -> None: print(f"  warn  {msg}")
def _fail(msg: str, state: dict) -> None:
    print(f"  FAIL  {msg}")
    state["fail"] = True


# Hook events registered by the installer, in canonical order.
_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")


def _claude_home() -> pathlib.Path:
    """Where global config lives — mirrors install.py's claude_home()."""
    return pathlib.Path(os.environ.get("CLAUDE_HOME", pathlib.Path.home() / ".claude"))


def _events_referencing_vault(settings_path: pathlib.Path, vault: pathlib.Path) -> list[str]:
    """Hook events in ``settings_path`` whose command references ``vault``.

    Content-based (substring on the vault path), never readlink — matching the
    installer's own hook-matching invariant. Returns [] on any error/absence.
    """
    if not settings_path.exists():
        return []
    import json
    try:
        hooks = json.loads(settings_path.read_text()).get("hooks", {})
    except Exception:
        return []
    needle = str(vault)
    found = []
    for ev in _HOOK_EVENTS:
        for group in hooks.get(ev, []):
            cmds = [h.get("command", "") for h in group.get("hooks", [])]
            if any(needle in c for c in cmds):
                found.append(ev)
                break
    return found


def _doctor_hooks() -> None:
    """Report hook registration, aware of BOTH install modes (global + local).

    Global: ~/.claude/settings.json holds absolute commands pointing at this
    vault. Local: the vault's own .claude/settings.json holds the hooks. A bare
    ~/.claude/settings.json that registers *some other* vault's hooks is not
    this vault's registration — hence the vault-path match, not a key check.
    """
    home_settings = _claude_home() / "settings.json"
    global_events = _events_referencing_vault(home_settings, REPO)
    if global_events:
        _ok(f"hooks registered globally ({home_settings}): {global_events}")
        return

    # Fall back to the project-local install mode.
    if SETTINGS.exists():
        import json
        try:
            hooks = json.loads(SETTINGS.read_text()).get("hooks", {})
            registered = [k for k in _HOOK_EVENTS if k in hooks]
            if registered:
                _ok(f"hooks registered locally ({SETTINGS}): {registered}")
            else:
                _warn("settings.json present but no hook keys")
        except Exception as e:
            _warn(f"settings.json unreadable: {e}")
        return

    # Neither mode active. If a .disabled project settings exists, this vault
    # was globally installed but its hooks now point elsewhere (e.g. a template
    # clone whose active vault is a different one) — say so plainly.
    if SETTINGS.with_suffix(".json.disabled").exists():
        _warn("hooks not registered for THIS vault "
              f"(project settings disabled; no global hooks in {home_settings} "
              "reference this path). Run install.py here to make it the active vault.")
    else:
        _warn("no hooks registered yet — run install.py to wire them up")


def doctor(verbose: bool = False) -> int:
    state = {"fail": False}
    print(f"doctor:")
    print(f"  db: {DB_PATH}")

    # 1. Preflight (env deps). Prefer the OS-neutral Python preflight; fall
    #    back to scripts/preflight.sh only when bash is available. Never hard-
    #    fail merely because `bash` is absent (Windows).
    ran_preflight = False
    try:
        sys.path.insert(0, str(REPO / "scripts" / "install"))
        import preflight as _preflight  # scripts/install/preflight.py
        rc = _preflight.run_preflight(REPO, verbose=verbose)
        ran_preflight = True
        if rc == 0:
            _ok("preflight")
        else:
            _fail(f"preflight (exit {rc})", state)
    except ModuleNotFoundError:
        pass
    if not ran_preflight:
        pre = REPO / "scripts" / "preflight.sh"
        bash = shutil.which("bash")
        if pre.exists() and bash:
            r = subprocess.run([bash, str(pre)], capture_output=True, text=True)
            if r.returncode == 0:
                _ok("preflight")
            else:
                _fail(f"preflight (exit {r.returncode})", state)
                if verbose:
                    print(r.stdout)
                    print(r.stderr)
        elif pre.exists() and not bash:
            _warn("preflight: bash unavailable and preflight.py absent — skipped")
        else:
            _warn("preflight.sh not present")

    # 2. DB exists / opens / has expected tables.
    try:
        conn = ensure_ready()
    except Exception as e:
        _fail(f"cannot open DB: {e}", state)
        return 1
    expected = {"pages", "citations", "pages_fts", "pages_vec",
                "conflicts", "pending_deltas", "session_cursors",
                "filed_sessions", "config", "schema_version"}
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
    missing = expected - have
    if missing:
        _fail(f"missing tables: {sorted(missing)}", state)
    else:
        _ok(f"schema ({len(expected)} tables)")

    # 3. Integrity check.
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ic == "ok":
        _ok("integrity_check")
    else:
        _fail(f"integrity_check: {ic}", state)

    # 4. sqlite-vec sanity.
    try:
        v = conn.execute("SELECT vec_version()").fetchone()[0]
        _ok(f"sqlite-vec {v}")
    except Exception as e:
        _fail(f"sqlite-vec unavailable: {e}", state)

    # 5. Ollama ping.
    try:
        with urllib.request.urlopen("http://localhost:11434/api/version", timeout=2) as r:
            v = r.read().decode()
        _ok(f"ollama {v.strip()}")
    except Exception as e:
        _warn(f"ollama unreachable ({e}) — injection will fail-open")

    # 6. Stale lock scan (only meaningful once wiki/ exists).
    stale = 0
    if WIKI.exists():
        cutoff = time.time() - 10 * 60  # 10 minutes
        for lock in WIKI.rglob(".*.lock"):
            try:
                if lock.stat().st_mtime < cutoff:
                    stale += 1
            except FileNotFoundError:
                pass
    if stale == 0:
        _ok(f"stale-locks (0)")
    else:
        _warn(f"stale-locks ({stale}) — run locks.py --sweep")

    # 7. Orphan deltas (rows in pending_deltas whose file is missing, or files without a row).
    orphans_row = conn.execute(
        "SELECT count(*) FROM pending_deltas WHERE merged_at IS NULL").fetchone()[0]
    if orphans_row:
        _warn(f"pending_deltas ({orphans_row} unmerged)")
    else:
        _ok("pending_deltas (0 unmerged)")

    # 8. Scorer log freshness — flag if it has grown in the last hour.
    if SCORER_LOG.exists():
        age = time.time() - SCORER_LOG.stat().st_mtime
        if age < 3600 and SCORER_LOG.stat().st_size > 0:
            _warn(f"scorer.log has recent activity (age {int(age)}s)")
        else:
            _ok(f"scorer.log (age {int(age)}s)")
    else:
        _ok("scorer.log (absent — no scorer runs yet)")

    # 9. Hook registration. Two install modes:
    #    - global: hooks live in ~/.claude/settings.json as absolute command
    #      strings pointing back at THIS vault's .claude/hooks/ (the project
    #      settings.json is renamed .disabled). This is the common mode.
    #    - project-local: hooks live in the vault's own .claude/settings.json.
    # Prefer the global signal (commands referencing this vault), then fall
    # back to the local file, else report an informative — not alarming — note.
    _doctor_hooks()

    return 1 if state["fail"] else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--init", action="store_true", help="create schema and exit")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if a.init:
        ensure_ready()
        print(f"initialised {DB_PATH}")
        return 0
    if a.doctor:
        return doctor(verbose=a.verbose)
    # Default action = ensure schema, then print a summary.
    conn = ensure_ready()
    n = conn.execute("SELECT count(*) FROM pages").fetchone()[0]
    print(f"{DB_PATH}: pages={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
