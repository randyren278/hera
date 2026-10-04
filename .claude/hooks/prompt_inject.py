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

Trust (design §5.1): what this hook emits is phrased as fact and lands inside
a privileged session, so the tier gate here is a security boundary, not a
relevance knob. Only INJECT_TRUST tiers may ever be emitted. The gate is
applied three times on purpose — at the search call, again on the merged list,
and once more per line as the pointer is formatted — because a single missed
check is a memory-poisoning hole, and each layer is cheap.

Note that "fail-open" describes the OUTPUT (print nothing on error), never the
FILTER: an error while establishing a page's tier withholds the page.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys


_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]


# Cheap heuristic: skip injection on prompts that are clearly pure coding /
# syntax questions where the vault has nothing to add. The cosine gate
# (config inject_min_cosine) is the real gate; this just avoids the embedding call.
CODING_PATTERNS = [
    r"^\s*(how do i|how can i|what's the syntax|what is the syntax)\b.*\b(regex|regexp|sed|awk|grep|git|npm|yarn|pnpm|cargo|pip|poetry|docker|kubectl|make|jq|curl|ffmpeg)\b",
    r"^\s*(write|generate|give me)\s+(a|an)?\s*(function|method|class|snippet|regex|regexp|bash|shell|python|javascript|typescript|rust|go)\b",
    r"^\s*(fix|debug)\s+(this|the|my)\b",
]
CODING_RE = re.compile("|".join(CODING_PATTERNS), re.I)

# The only tiers this hook may surface into a privileged session. Named here
# rather than taken from search.DEFAULT_TRUST so that widening the search
# default can never silently widen what gets injected.
INJECT_TRUST = ("self", "team")


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


def _hera_off() -> bool:
    v = os.environ.get("HERA_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _hera_off():
        return 0
    try:
        # --no-ollama override for e2e tests: simulate Ollama being unreachable.
        if os.environ.get("HERA_INJECT_NO_OLLAMA") == "1":
            # Fail-open: emit nothing, exit 0.
            return 0

        prompt = _load_prompt()
        if not prompt or len(prompt) < 4:
            return 0

        if CODING_RE.search(prompt):
            return 0

        sys.path.insert(0, str(REPO / "scripts"))
        import hera_db  # type: ignore
        import search as _search  # type: ignore

        import embed as _embed  # type: ignore

        conn = hera_db.connect()
        cfg = dict(conn.execute("SELECT key, value FROM config").fetchall())
        top_n = int(cfg.get("inject_top_n", 3))
        floor = float(cfg.get("inject_relevance_floor", 0.015))
        min_cos = float(cfg.get("inject_min_cosine", 0.65))
        # Candidates beyond top_n, so the cosine gate below can drop weak ones
        # without starving the result.
        fetch_n = max(top_n * 4, 12)

        # One query embedding for both stores, with a budget that fits the
        # 10 s hook timeout (no retry backoff: a dead Ollama fails fast).
        qvec = _embed.embed_query(prompt, timeout=4.0, retries=1)

        hits = _search.hybrid_search(conn, prompt, top_n=fetch_n, floor=floor,
                                     trust_in=INJECT_TRUST, qvec=qvec)

        # Team side: same hybrid substrate over team.db, owner-tagged — only
        # when a team index exists (team_sync builds it), and opened without
        # creating anything. Its own try/except so a broken team.db degrades
        # to local-only. Team pages never enter hera.db (ADR-14).
        team_hits: list = []
        try:
            import team_index  # type: ignore
            if team_index.TEAM_DB.exists():
                tconn = hera_db.connect(team_index.TEAM_DB)
                team_hits = _search.team_hybrid_search(tconn, prompt, top_n=fetch_n,
                                                        floor=floor,
                                                        trust_in=INJECT_TRUST,
                                                        qvec=qvec)
        except Exception:
            team_hits = []

        # Normalise both sides into (title, path, page_id, score, owner|None) and
        # fuse into ONE ranked list — team and local RRF scores share a scale.
        merged: list[dict] = []
        for h in hits:
            merged.append({"title": h.title, "path": h.path,
                           "page_id": h.page_id, "score": h.score, "owner": None,
                           "trust": getattr(h, "trust", "untrusted"),
                           "cosine": h.cosine})
        for t in team_hits:
            # Everything in team.db is team-tier by construction (ADR-14).
            merged.append({"title": t["title"], "path": t["path"],
                           "page_id": t["page_id"], "score": t["score"],
                           "owner": t.get("owner"), "trust": "team",
                           "cosine": t.get("cosine")})

        # Second gate. hybrid_search already filtered, but this list is the one
        # that becomes text in a privileged session, so it is re-checked here
        # rather than trusted.
        merged = [m for m in merged if m["trust"] in INJECT_TRUST]
        # Relevance gate: a pointer needs real semantic similarity. Keyword-only
        # matches (no dense candidate) and weak neighbours are noise.
        merged = [m for m in merged if m["cosine"] is not None and m["cosine"] >= min_cos]
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
        # The scorer credits only pages cited in the final answer; stating that
        # here (as fact) is what makes citations happen at all.
        lines = ["Relevant vault pages (pointers only — read the file if needed). "
                 "A page is credited when the final answer cites it as (Source: [[Title]]):"]
        for m in merged:
            one_line = _first_line_from(REPO / m["path"])
            # Emit the ABSOLUTE path (REPO is the vault root). Hooks run from any
            # cwd under the global install; a bare vault-relative path can't be
            # resolved from a non-vault cwd, and an agent that fails to open it
            # wrongly concludes the index is stale. The absolute path resolves
            # everywhere. Team paths are repo-relative under team-staging/,
            # so REPO / path resolves them too.
            abs_path = (REPO / m["path"]).as_posix()
            # Third gate — nothing becomes a line without its tier re-checked.
            if m["trust"] not in INJECT_TRUST:
                continue
            # Team-tier pointers are delimited with their attribution FIRST, so
            # the provenance of a page someone else wrote is read before its
            # content, not discovered at the end of the line.
            if m["trust"] == "team":
                attribution = f"(team: {m['owner']}) " if m["owner"] else "(team) "
            else:
                attribution = ""
            lines.append(
                f"- {attribution}[[{m['title']}]]  ({abs_path})  — {one_line}")
            for co, cn in conflicts_by_page.get(m["page_id"], []):
                lines.append(f"    ⚠ contested — existing: {co} · new: {cn} · unresolved.")
        if len(lines) == 1:
            return 0  # header only — every candidate was gated out.
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
