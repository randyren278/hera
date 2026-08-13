"""publish.py — /brain-team add engine (design §9.2, ADR-08).

Public pipeline: ingest privately (using existing ingest.py), then strip the
result down to a public-safe subset, stage into team-brain-staging/<owner>/,
and gate the actual git push behind a human diff review.

Strip rules (LLM-driven — the human diff review is the safety mechanism):
  - remove personal information
  - remove references to named private individuals
  - remove subjective claims and opinions
  - keep facts, frameworks, definitions, technical patterns

Every public-vault page gains `visibility: public` frontmatter (§4.2).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402
import ingest as _ingest  # noqa: E402

# Nested-call configuration is defined once, in ingest. Re-exported here so
# this module's call site and its tests can refer to the same values.
# NOTE: these are a snapshot taken at import time, not a live binding -- a
# test that patches ingest via monkeypatch.setattr (rather than
# importlib.reload) will leave these publish.CLAUDE_* values stale.
CLAUDE_MODEL = _ingest.CLAUDE_MODEL
CLAUDE_ISOLATION = _ingest.CLAUDE_ISOLATION
CLAUDE_CWD = _ingest.CLAUDE_CWD

import team_sync  # noqa: E402  (shared remote resolver + no-team message)

STAGING = REPO / "team-brain-staging"
OWNER = os.environ.get("BRAIN_OWNER", "randy")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"


def _no_team_space() -> bool:
    """A team space exists if a remote is configured OR a staging clone is
    already on disk. Absent both, team commands are a clean no-op."""
    return team_sync._resolve_remote() is None and not (STAGING / ".git").exists()


STRIP_PROMPT = """\
You are the public-vault redactor for a personal knowledge system. You are
given the FULL markdown body of a private-vault page. Return a stripped
public-safe version — same file, edited.

Redaction rules (defense in depth; the human still reviews the diff):
  - REMOVE any personal information (names, addresses, employers, etc.)
  - REMOVE mentions of named private individuals
  - REMOVE subjective claims and opinions (e.g. "I think", "in my view",
    "this is bad", "this is great")
  - KEEP facts, technical patterns, framework definitions, generic entities
    (well-known public companies, projects, and papers are OK).
  - KEEP wikilinks; only strip the page body they point to if the target is
    itself private.

Return ONLY the stripped markdown body — no code fence, no preamble.
If the page is not safe to publish at all, return the single token: SKIP.

PRIVATE PAGE:
---
{body}
---
"""


def _strip_body(body: str) -> str | None:
    """Return stripped markdown, or None to skip this page entirely."""
    prompt = STRIP_PROMPT.format(body=body[:20000])
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=600, cwd=CLAUDE_CWD)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    if out.startswith("```"):
        out = re.sub(r"^```\w*\s*", "", out)
        out = re.sub(r"\s*```$", "", out)
    if out.strip().upper() == "SKIP":
        return None
    return out.strip() + "\n"


def _upsert_public_page(target: pathlib.Path, page_id: str, title: str,
                        aliases: list[str], type_: str, stripped_body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fm = _ingest._frontmatter(page_id, title, type_,
                              extra={"visibility": "public", "owner": OWNER},
                              aliases=aliases)
    target.write_text(fm + stripped_body, encoding="utf-8")


def stage_private_ingest(source_path: str, source_kind: str = "file") -> dict:
    """Run the private ingest, then strip each produced page into staging.

    Returns a summary with:
      staged: list of relative paths written under team-brain-staging/<owner>/
      skipped: list of (page_title, reason) tuples the redactor dropped
      warnings: list of ingest warnings
    """
    result = _ingest.ingest_source(source_path, source_kind=source_kind)

    STAGING.mkdir(parents=True, exist_ok=True)
    owner_dir = STAGING / OWNER
    owner_dir.mkdir(parents=True, exist_ok=True)

    staged: list[str] = []
    skipped: list[tuple[str, str]] = []

    # For each page produced by the private ingest, strip and stage.
    all_pages = [(result.source, "sources")] \
                + [(p, "concepts") for p in result.concepts] \
                + [(p, "entities") for p in result.entities]

    for pw, subdir in all_pages:
        # Read the private-vault page body to strip.
        priv_path = REPO / pw.path.relative_to(REPO)
        if not priv_path.exists():
            # Frozen page (contradiction detected during ingest) — skip.
            skipped.append((pw.title, "frozen (contradiction pending)"))
            continue
        body = priv_path.read_text(encoding="utf-8")
        # Strip frontmatter for stripping.
        if body.startswith("---\n"):
            end = body.find("\n---\n", 4)
            if end != -1:
                body = body[end + 5:]

        stripped = _strip_body(body)
        if stripped is None:
            skipped.append((pw.title, "redactor said SKIP"))
            continue

        target = owner_dir / subdir / f"{_ingest._slugify(pw.title)}.md"
        _upsert_public_page(target, pw.id, pw.title, pw.aliases, pw.type, stripped)
        staged.append(target.relative_to(STAGING).as_posix())

    return {
        "staged": staged,
        "skipped": skipped,
        "warnings": result.warnings,
    }


def render_diff() -> str:
    """Render the staging clone's uncommitted diff. Returns a unified diff string."""
    owner_dir = STAGING / OWNER
    if not (STAGING / ".git").exists():
        return "(team-brain-staging is not a git repo)"
    r = subprocess.run(["git", "-C", str(STAGING), "add", "-A"],
                       capture_output=True, text=True)
    r2 = subprocess.run(["git", "-C", str(STAGING), "diff", "--cached", "--no-color"],
                        capture_output=True, text=True)
    return r2.stdout


def commit_and_push(commit_msg: str) -> str:
    """Commit staged changes and push. Returns 'ok' or an error message."""
    if not (STAGING / ".git").exists():
        return "team-brain-staging is not a git repo"
    subprocess.run(["git", "-C", str(STAGING), "add", "-A"], check=True)
    r = subprocess.run(["git", "-C", str(STAGING), "commit", "-m", commit_msg],
                       capture_output=True, text=True)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        return f"commit failed: {r.stderr}"
    p = subprocess.run(["git", "-C", str(STAGING), "push"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return f"push failed: {p.stderr}"
    return "ok"


def _cli() -> int:
    if _no_team_space():
        print(team_sync.NO_TEAM_MSG)
        return 0
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_stage = sub.add_parser("stage")
    p_stage.add_argument("source")
    p_stage.add_argument("--kind", default="file")
    p_stage.add_argument("--json", action="store_true")
    sub.add_parser("diff")
    p_push = sub.add_parser("push")
    p_push.add_argument("--message", "-m", default="brain: public update")
    a = ap.parse_args()

    if a.cmd == "stage":
        summary = stage_private_ingest(a.source, a.kind)
        if a.json:
            print(json.dumps(summary, indent=2, default=str))
        else:
            print(f"staged {len(summary['staged'])} pages:")
            for s in summary["staged"]:
                print(f"  {s}")
            if summary["skipped"]:
                print("skipped:")
                for t, r in summary["skipped"]:
                    print(f"  {t}: {r}")
        return 0

    if a.cmd == "diff":
        d = render_diff()
        print(d)
        return 0

    if a.cmd == "push":
        r = commit_and_push(a.message)
        print(r)
        return 0 if r == "ok" else 1

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
