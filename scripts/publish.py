"""publish.py — /hera-team add engine (design §9.2, ADR-08).

Public pipeline: ingest privately (using existing ingest.py), then strip the
result down to a public-safe subset, stage into team-staging/<owner>/,
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
import hashlib
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
import hera_db  # noqa: E402
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

STAGING = team_sync.STAGING  # honors $HERA_VAULT like every team engine
OWNER = team_sync.owner()  # None → team writes refuse (team_sync.OWNER_MISSING)
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
  - UNLINK wikilinks: replace each [[Page Title]] with plain text, and drop
    it entirely if the title itself names a private person, project, or
    employer — a link to a page that is not published leaks its title.

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
    cmd = _ingest._model_command()
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
      staged: list of relative paths written under team-staging/<owner>/
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


# Deterministic secret scan over ADDED lines of the staged diff. The LLM
# redactor is not a security boundary; these patterns are. BLOCK stops a push;
# WARN is shown in the diff for the human reviewer.
BLOCK_PATTERNS = [
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("Slack token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("Anthropic/OpenAI key", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("credential assignment",
     r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]
WARN_PATTERNS = [
    ("email address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("home-directory path", r"(?:/Users/|/home/|C:\\Users\\)[^/\\\s]+"),
]


def scan_diff(diff: str) -> tuple[list[str], list[str]]:
    """(blocking, warnings) findings on the diff's added lines."""
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    def hits(patterns):
        out = []
        for name, rx in patterns:
            for line in added:
                if re.search(rx, line):
                    out.append(f"{name}: {line.strip()[:120]}")
                    break
        return out
    return hits(BLOCK_PATTERNS), hits(WARN_PATTERNS)


def review_token(diff: str) -> str:
    """Fingerprint of exactly the diff a human reviewed; push must quote it."""
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()[:12]


def staged_diff() -> str:
    team_sync._run(["git", "add", "-A"], cwd=STAGING)
    return team_sync._run(["git", "diff", "--cached", "--no-color"], cwd=STAGING).stdout


def render_diff() -> str:
    """The staging clone's pending diff, followed by scan findings and the
    review token that `push --confirm` requires."""
    if not (STAGING / ".git").exists():
        return "(team-staging is not a git repo)"
    diff = staged_diff()
    if not diff.strip():
        return "(nothing staged)"
    blocking, warnings = scan_diff(diff)
    tail = [""]
    tail += [f"BLOCKED — likely secret ({b})" for b in blocking]
    tail += [f"warning — review: {w}" for w in warnings]
    tail.append(f"review-token: {review_token(diff)}")
    return diff + "\n".join(tail)


def commit_and_push(commit_msg: str, confirm: str | None) -> str:
    """Commit staged changes and push — only if ``confirm`` is the review token
    of the diff staged right now and the secret scan is clean. Returns 'ok' or
    an error message."""
    if not (STAGING / ".git").exists():
        return "team-staging is not a git repo"
    diff = staged_diff()
    if not diff.strip():
        return "nothing staged to push"
    if confirm != review_token(diff):
        return ("refused: --confirm must be the review-token printed by `diff` for the "
                "content staged now (none given, or the staged content changed since "
                "review). Re-run diff, have the human review it, then push --confirm <token>.")
    blocking, _ = scan_diff(diff)
    if blocking:
        return "refused: likely secret in the staged diff — " + "; ".join(blocking)
    r = team_sync._run(["git", "commit", "-m", commit_msg], cwd=STAGING)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        return f"commit failed: {r.stderr}"
    p = team_sync._run(["git", "push"], cwd=STAGING)
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
    p_push.add_argument("--message", "-m", default="hera: public update")
    p_push.add_argument("--confirm", metavar="REVIEW_TOKEN",
                        help="the review-token `diff` printed for the reviewed content")
    a = ap.parse_args()

    if a.cmd == "stage" and not OWNER:
        print(team_sync.OWNER_MISSING, file=sys.stderr)
        return 2

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
        r = commit_and_push(a.message, a.confirm)
        print(r)
        return 0 if r == "ok" else 1

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
