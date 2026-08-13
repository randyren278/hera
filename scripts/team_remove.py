#!/usr/bin/env python3
"""team_remove.py — un-publish YOUR OWN pages from the shared team brain.

The team brain is a plain git repo of redacted Markdown, one folder per owner,
cloned into `team-brain-staging/`. This engine is the *remove* side of the loop:
it lists the pages YOU published (owner-scoped) and stages git-rm deletions,
confined to `team-brain-staging/<owner>/`.

Safety invariants (mirror the publish gate):
  - OWNER is resolved EXACTLY like publish.py: BRAIN_OWNER env, default "randy".
    The staging folder is team-brain-staging/<owner>/, NOT git user.name.
  - Owner-scoped: stage-remove refuses ANY path outside team-brain-staging/<owner>/.
    A single out-of-scope path aborts the whole call and stages nothing (fail-closed).
  - It NEVER publishes to the remote. Sending staged changes upstream routes
    through publish.py (one gated, human-reviewed path). Git history is the undo
    — no .archive mirror on the team side.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

_env_vault = os.environ.get("SECOND_BRAIN_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import team_sync  # noqa: E402  (shared remote resolver + no-team message)

STAGING = REPO / "team-brain-staging"
OWNER = os.environ.get("BRAIN_OWNER", "randy")
OWNER_DIR = STAGING / OWNER


def _no_team_space() -> bool:
    """A team space exists if a remote is configured OR a staging clone is
    already on disk. Absent both, team commands are a clean no-op."""
    return team_sync._resolve_remote() is None and not (STAGING / ".git").exists()


def _run(args: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)


def _in_owner_scope(path: str) -> bool:
    """True iff `path` resolves to a location inside team-brain-staging/<owner>/.

    Standalone so tests can assert the guard directly. Fail-closed: anything
    that doesn't resolve strictly under OWNER_DIR is out of scope.
    """
    try:
        p = pathlib.Path(path).resolve()
        owner_root = OWNER_DIR.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        p.relative_to(owner_root)
        return True
    except ValueError:
        return False


def _title(md_text: str, fallback: str) -> str:
    """Pull the frontmatter title (shallow), else fallback to the file stem."""
    if md_text.startswith("---\n"):
        end = md_text.find("\n---\n", 4)
        if end != -1:
            for line in md_text[4:end].splitlines():
                if line.startswith("title:"):
                    return line[len("title:"):].strip().strip('"')
    return fallback


def _added_epoch(relpath: str) -> int:
    """Added/last-touch epoch for a staged page via git; fallback to file mtime."""
    if (STAGING / ".git").exists():
        r = _run(["git", "log", "-1", "--format=%at", "--", relpath], cwd=STAGING)
        out = r.stdout.strip()
        if r.returncode == 0 and out.isdigit():
            return int(out)
    try:
        return int((STAGING / relpath).stat().st_mtime)
    except OSError:
        return 0


def _epoch_to_date(epoch: int) -> str:
    import time
    if not epoch:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def list_pages() -> list[dict]:
    """Return the caller's own published pages, newest first.

    Each item: {title, path (repo-relative), added_epoch, added_date, type}.
    Owner-scoped by construction — walks OWNER_DIR only.
    """
    pages: list[dict] = []
    if not OWNER_DIR.exists():
        return pages
    for f in OWNER_DIR.rglob("*.md"):
        if "/.git/" in f.as_posix():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rel_staging = f.relative_to(STAGING).as_posix()  # e.g. randy/concepts/x.md
        epoch = _added_epoch(rel_staging)
        # type = parent folder under the owner dir (sources|concepts|entities)
        parts = f.relative_to(OWNER_DIR).parts
        type_ = parts[0] if len(parts) > 1 else "other"
        pages.append({
            "title": _title(text, f.stem),
            "path": f.relative_to(REPO).as_posix(),
            "added_epoch": epoch,
            "added_date": _epoch_to_date(epoch),
            "type": type_,
        })
    pages.sort(key=lambda p: p["added_epoch"], reverse=True)
    return pages


def stage_remove(paths: list[str]) -> dict:
    """Stage git-rm deletions for the given pages. Fail-closed on any out-of-scope
    path: if ANY path is outside team-brain-staging/<owner>/, abort and stage nothing.

    Returns {staged_removals: [...], refused: [...]}. NEVER sends to the remote.
    """
    refused = [p for p in paths if not _in_owner_scope(_abs(p))]
    if refused:
        return {"staged_removals": [], "refused": refused,
                "error": "out-of-scope path(s) — owner-scoped removal aborted, nothing staged"}

    if not (STAGING / ".git").exists():
        return {"staged_removals": [], "refused": [],
                "error": "team-brain-staging is not a git repo"}

    staged: list[str] = []
    for p in paths:
        abs_p = _abs(p)
        rel_staging = pathlib.Path(abs_p).resolve().relative_to(STAGING.resolve()).as_posix()
        r = _run(["git", "rm", "--", rel_staging], cwd=STAGING)
        if r.returncode != 0:
            return {"staged_removals": staged, "refused": [],
                    "error": f"git rm failed for {rel_staging}: {r.stderr.strip()}"}
        staged.append(rel_staging)
    return {"staged_removals": staged, "refused": []}


def _abs(p: str) -> str:
    """Resolve a user-supplied path to absolute. Repo-relative and absolute both work."""
    pp = pathlib.Path(p)
    if pp.is_absolute():
        return str(pp)
    # Try as-is (cwd-relative), then as repo-relative.
    if pp.exists():
        return str(pp.resolve())
    return str((REPO / p).resolve())


def diff() -> str:
    """Show the staging clone's staged diff (what publishing would send upstream)."""
    if not (STAGING / ".git").exists():
        return "(team-brain-staging is not a git repo)"
    _run(["git", "add", "-A"], cwd=STAGING)
    r = _run(["git", "diff", "--cached", "--no-color"], cwd=STAGING)
    return r.stdout


def _cli() -> int:
    if _no_team_space():
        print(team_sync.NO_TEAM_MSG)
        return 0
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")

    p_rm = sub.add_parser("stage-remove")
    p_rm.add_argument("paths", nargs="+")
    p_rm.add_argument("--json", action="store_true")

    sub.add_parser("diff")

    a = ap.parse_args()

    if a.cmd == "list":
        pages = list_pages()
        if a.json:
            print(json.dumps(pages, indent=2))
        else:
            if not pages:
                print(f"no published pages under team-brain-staging/{OWNER}/")
                return 0
            print(f"your published pages (owner={OWNER}), newest first:")
            for p in pages:
                print(f"  [{p['added_date']}] {p['type']:<8} {p['title']}  ({p['path']})")
        return 0

    if a.cmd == "stage-remove":
        result = stage_remove(a.paths)
        if a.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("error"):
                print(f"refused: {result['error']}")
                for r in result.get("refused", []):
                    print(f"  out-of-scope: {r}")
            for s in result.get("staged_removals", []):
                print(f"  staged removal: {s}")
        return 1 if result.get("error") else 0

    if a.cmd == "diff":
        print(diff())
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
