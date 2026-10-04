#!/usr/bin/env python3
"""team_sync.py — bring the shared team space down to disk.

The team space is a plain git repo of redacted Markdown, one folder per
owner. This helper is the *pull* side of the loop: it clones the
user-configured remote into `team-staging/` if absent, else
fast-forward-pulls.

There is no fixed remote. The remote URL is per-machine, resolved from
`HERA_TEAM_REMOTE` (set in `~/.claude/hera.env` by
`/hera-setup`). If it is unset, no team space is configured and every
team path is a clean no-op.

It NEVER pushes. Publishing (push) stays a human-approved action in
`publish.py` / `/hera-team add`. Retrieval only ever reads.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[1]
STAGING = REPO / "team-staging"

NO_TEAM_MSG = "No team space configured — run /hera-setup to add one."

# OS/editor junk that must never be swept into a publish. The staging clone is
# a SEPARATE git repo from the vault, so the vault's own .gitignore does not
# apply here — the engines' `git add -A` (publish.py, team_remove.py) would
# otherwise stage a stray .DS_Store. We ship this file into the clone so those
# files are ignored at the source.
_STAGING_GITIGNORE = """\
# Managed by team_sync.py — keeps OS/editor junk out of the shared team space.
.DS_Store
.idea/
.vscode/
*.swp
"""


def _ensure_staging_gitignore() -> None:
    """Write team-staging/.gitignore if absent or out of date. Idempotent."""
    gi = STAGING / ".gitignore"
    try:
        if gi.exists() and gi.read_text(encoding="utf-8") == _STAGING_GITIGNORE:
            return
        STAGING.mkdir(parents=True, exist_ok=True)
        gi.write_text(_STAGING_GITIGNORE, encoding="utf-8")
    except OSError:
        pass  # fail-open: a missing ignore file is not worth aborting a sync



def _locator_value(key: str) -> str | None:
    """A per-machine setting: process env first (an explicitly empty value
    means unset), else the same key in the locator file (CLAUDE_HOME-aware),
    which a bare subprocess may not have inherited."""
    if key in os.environ:
        return os.environ[key].strip() or None
    home = pathlib.Path(os.environ.get("CLAUDE_HOME", pathlib.Path.home() / ".claude"))
    env_file = home / "hera.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            k, sep, rhs = line.partition("=")
            if sep and k.strip() == key:
                rhs = rhs.strip().strip('"').strip("'")
                if rhs:
                    return rhs
    return None


def _resolve_remote() -> str | None:
    """The team space remote URL (`HERA_TEAM_REMOTE`), or None if unset."""
    return _locator_value("HERA_TEAM_REMOTE")


OWNER_MISSING = ("HERA_OWNER is not set — add HERA_OWNER=\"<your name>\" to "
                 "~/.claude/hera.env (/hera-setup asks for it). Team writes need "
                 "it so you publish under your own folder, never someone else's.")


def owner() -> str | None:
    """Your team-space folder name (`HERA_OWNER`). No default: a guessed owner
    would publish into, or remove from, someone else's folder."""
    return _locator_value("HERA_OWNER")


GIT_TIMEOUT_S = 120


def _run(args: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    """Run git non-interactively with a deadline: a credential prompt or a dead
    network must fail the command, never hang the agent that called it."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, env=env, timeout=GIT_TIMEOUT_S,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, 124, "", f"timed out after {GIT_TIMEOUT_S}s: {' '.join(args)}\n")


def _reindex_after_sync() -> None:
    """Refresh team.db from the freshly-synced Markdown. Embedding cost is paid
    here (on sync), not per query. A reindex failure (e.g. Ollama down) must
    NEVER fail the sync — warn and move on. Imported lazily to avoid a hard
    dependency when team_sync is used without the index engine."""
    try:
        import team_index  # lazy: keeps sync usable if the index engine is absent
        n = team_index.reindex(changed_only=True)
        print(f"reindexed {n} changed team page(s) into team.db")
    except Exception as e:
        sys.stderr.write(f"(team.db reindex skipped: {e})\n")


def clone_or_pull() -> int:
    """Clone the remote if STAGING isn't a repo yet, else ff-pull, then refresh
    team.db from the synced Markdown. Returns rc."""
    rc = _clone_or_pull_git()
    if rc == 0:
        _reindex_after_sync()
    return rc


def _clone_or_pull_git() -> int:
    """The git side of the sync (clone or ff-pull). Returns rc."""
    remote = _resolve_remote()
    if not remote:
        print(NO_TEAM_MSG)
        return 0

    if (STAGING / ".git").exists():
        r = _run(["git", "pull", "--ff-only"], cwd=STAGING)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            # Empty remote (no upstream branch yet) is not a failure.
            msg = (r.stdout + r.stderr).lower()
            if "no such ref" in msg or "couldn't find remote ref" in msg \
               or "no commits yet" in msg or "not have any commits" in msg:
                print("(remote is empty — nothing to pull)")
                _ensure_staging_gitignore()
                return 0
            sys.stderr.write(r.stderr)
            return r.returncode
        print("pulled team-staging (ff-only)")
        _ensure_staging_gitignore()
        return 0

    # Not a repo yet. Clone, preserving any pre-existing local pages.
    if STAGING.exists() and any(STAGING.iterdir()):
        # Convert an existing plain folder into a clone without data loss.
        rc = _init_over_existing(remote)
        if rc == 0:
            _ensure_staging_gitignore()
        return rc

    r = _run(["git", "clone", remote, str(STAGING)])
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        return r.returncode
    print("cloned team-staging")
    _ensure_staging_gitignore()
    return 0


def _init_over_existing(remote: str) -> int:
    """STAGING has local files but no .git — init a repo and wire the remote."""
    for args in (
        ["git", "init"],
        ["git", "remote", "add", "origin", remote],
    ):
        r = _run(args, cwd=STAGING)
        if r.returncode != 0 and "already exists" not in (r.stdout + r.stderr):
            sys.stderr.write(r.stderr)
            return r.returncode
    # Try to fetch + set an upstream if the remote has anything.
    _run(["git", "fetch", "origin"], cwd=STAGING)
    print("initialized team-staging over existing files (remote wired)")
    return 0


def check(url: str | None) -> int:
    """Access probe: `git ls-remote <url>`. Exit 0 if reachable (even if the
    repo is empty), non-zero otherwise. Read-only — never clones."""
    if not url or not url.strip():
        sys.stderr.write("usage: team_sync.py check <git-url>\n")
        return 2
    r = _run(["git", "ls-remote", url.strip()])
    if r.returncode == 0:
        print(f"reachable: {url.strip()}")
        return 0
    sys.stderr.write(r.stderr or "unreachable\n")
    return r.returncode or 1


def _cli() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "clone-or-pull"
    if cmd == "clone-or-pull":
        return clone_or_pull()
    if cmd == "check":
        return check(sys.argv[2] if len(sys.argv) > 2 else None)
    sys.stderr.write(f"unknown command: {cmd}\n")
    sys.stderr.write("commands: clone-or-pull | check <git-url>\n")
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
