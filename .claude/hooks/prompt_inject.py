#!/usr/bin/env python3
"""prompt_inject.py — UserPromptSubmit hook (design §8.2, ADR-04).

Per-turn hybrid-search injection. Given the user's prompt on stdin (JSON),
emit a small "pointer" block listing the top-N vault pages relevant to the
prompt: title + one-line + path — NOT the full page. Claude reads the page
itself if it needs the body.

Fail-open policy: if Ollama is down, the DB is missing, or any exception
occurs, print nothing and exit 0. Context injection must never break a
session (design §8.2, R-6).

CP-5 will extend this to append a ⚠ contested warning for any retrieved
page with an open conflict.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys


_env_vault = os.environ.get("SECOND_BRAIN_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]


# Cheap heuristic: skip injection on prompts that are clearly pure coding /
# syntax questions where the vault has nothing to add. The relevance floor
# in search.py provides the real gate; this just avoids the embedding call.
CODING_PATTERNS = [
    r"^\s*(how do i|how can i|what's the syntax|what is the syntax)\b.*\b(regex|regexp|sed|awk|grep|git|npm|yarn|pnpm|cargo|pip|poetry|docker|kubectl|make|jq|curl|ffmpeg)\b",
    r"^\s*(write|generate|give me)\s+(a|an)?\s*(function|method|class|snippet|regex|regexp|bash|shell|python|javascript|typescript|rust|go)\b",
    r"^\s*(fix|debug)\s+(this|the|my)\b",
]
CODING_RE = re.compile("|".join(CODING_PATTERNS), re.I)


def _load_prompt() -> str:
    raw = sys.stdin.read().strip()
    if not raw:
        return ""
    try:
        evt = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # tolerate plain-text stdin for testing
    # Claude Code UserPromptSubmit event carries the prompt under `prompt`.
    return (evt.get("prompt")
            or evt.get("user_prompt")
            or evt.get("text")
            or "")


def _brain_off() -> bool:
    v = os.environ.get("SECOND_BRAIN_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _brain_off():
        return 0
    try:
        # --no-ollama override for e2e tests: simulate Ollama being unreachable.
        if os.environ.get("BRAIN_INJECT_NO_OLLAMA") == "1":
            # Fail-open: emit nothing, exit 0.
            return 0

        prompt = _load_prompt()
        if not prompt or len(prompt) < 4:
            return 0

        if CODING_RE.search(prompt):
            return 0

        sys.path.insert(0, str(REPO / "scripts"))
        import brain_db  # type: ignore
        import search as _search  # type: ignore

        conn = brain_db.connect()
        top_n = int(conn.execute(
            "SELECT value FROM config WHERE key='inject_top_n'"
        ).fetchone()[0])
        floor = float(conn.execute(
            "SELECT value FROM config WHERE key='inject_relevance_floor'"
        ).fetchone()[0])

        hits = _search.hybrid_search(conn, prompt, top_n=top_n, floor=floor)

        # Team side: same hybrid substrate over team.db, owner-tagged. Its own
        # try/except so a broken team.db / dead Ollama degrades to local-only
        # (the outer except is the final backstop). Team pages never enter
        # brain.db — they live in a separate index (isolation invariant).
        team_hits: list = []
        try:
            import team_index  # type: ignore
            tconn = team_index.open_team_db()
            team_hits = _search.team_hybrid_search(tconn, prompt, top_n=top_n,
                                                    floor=floor)
        except Exception:
            team_hits = []

        # Normalise both sides into (title, path, page_id, score, owner|None) and
        # fuse into ONE ranked list — team and local RRF scores share a scale.
        merged: list[dict] = []
        for h in hits:
            merged.append({"title": h.title, "path": h.path,
                           "page_id": h.page_id, "score": h.score, "owner": None})
        for t in team_hits:
            merged.append({"title": t["title"], "path": t["path"],
                           "page_id": t["page_id"], "score": t["score"],
                           "owner": t.get("owner")})
        if not merged:
            return 0
        merged.sort(key=lambda m: (-m["score"], m["title"]))
        merged = merged[:top_n]

        # Any retrieved LOCAL page with an open conflict gets a ⚠ warning inline.
        local_ids = [m["page_id"] for m in merged if m["owner"] is None]
        conflicts_by_page: dict[str, list[tuple[str, str]]] = {}
        if local_ids:
            placeholders = ",".join(["?"] * len(local_ids))
            rows = conn.execute(
                f"SELECT page_id, claim_old, claim_new FROM conflicts "
                f"WHERE status='open' AND page_id IN ({placeholders})",
                tuple(local_ids),
            ).fetchall()
            for pid, co, cn in rows:
                conflicts_by_page.setdefault(pid, []).append((co, cn))

        # Format as factual statements (not imperatives) to sidestep injection defenses (R-8).
        lines = ["Relevant vault pages (pointers only — read the file if needed):"]
        for m in merged:
            one_line = _first_line_from(REPO / m["path"])
            # Emit the ABSOLUTE path (REPO is the vault root). Hooks run from any
            # cwd under the global install; a bare vault-relative path can't be
            # resolved from a non-vault cwd, and an agent that fails to open it
            # wrongly concludes the index is stale. The absolute path resolves
            # everywhere. Team paths are repo-relative under team-brain-staging/,
            # so REPO / path resolves them too.
            abs_path = (REPO / m["path"]).as_posix()
            tag = f" (team: {m['owner']})" if m["owner"] else ""
            lines.append(f"- [[{m['title']}]]  ({abs_path})  — {one_line}{tag}")
            for co, cn in conflicts_by_page.get(m["page_id"], []):
                lines.append(f"    ⚠ contested — existing: {co} · new: {cn} · unresolved.")
        sys.stdout.write("\n".join(lines) + "\n")
    except Exception:
        # Fail-open: swallow everything.
        pass
    return 0


def _first_line_from(path: pathlib.Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Skip frontmatter
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + 5:]
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith(">"):
                return s[:160]
    except Exception:
        pass
    return ""


if __name__ == "__main__":
    sys.exit(main())
