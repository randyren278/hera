#!/usr/bin/env python3
"""session_start.py — SessionStart hook (design §8.1).

Fires on source: startup | resume | clear | compact. Emits:
  1. Full contents of wiki/hot.md
  2. Open conflicts scoped to the current cwd (CP-5+) — count-only for now
  3. Pages with pending deltas > 24h (CP-1 machinery, safe now)

Reads event JSON from stdin. Prints context to stdout. Never fails: any
exception is swallowed and prints nothing — a broken hook must not block
starting a session.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time


_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]
WIKI = REPO / "wiki"


def _hera_off() -> bool:
    v = os.environ.get("HERA_OFF", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def main() -> int:
    if _hera_off():
        return 0
    try:
        raw = sys.stdin.read()
        try:
            evt = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            evt = {}
        cwd = evt.get("cwd") or os.getcwd()
        # Normalize for the conflict-scope equality match: origin_cwd is stored
        # normalized (ingest.py), so query with the same normalization or the
        # `=` comparison silently misses on Windows (case/separator drift).
        cwd = os.path.normcase(os.path.normpath(cwd))

        pieces: list[str] = []

        # 1) hot cache
        hot = WIKI / "hot.md"
        if hot.exists():
            text = hot.read_text(encoding="utf-8", errors="replace")
            pieces.append(text.rstrip())
        else:
            pieces.append("# Recent Context\n\n(vault empty — no hot cache yet)")

        # 2) open conflicts — origin-scoped get full claim text + raise-with-user
        #    instruction (channel 2); others get a quiet count (elsewhere).
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            import hera_db  # type: ignore
            conn = hera_db.connect()
            scoped = conn.execute(
                "SELECT c.id, c.claim_old, c.claim_new, p.title "
                "FROM conflicts c JOIN pages p ON p.id = c.page_id "
                "WHERE c.status = 'open' AND (c.origin_cwd = ? OR c.origin_cwd IS NULL)",
                (cwd,),
            ).fetchall()
            if scoped:
                pieces.append("\n## Open conflicts from this project (please raise with the user immediately)")
                for cid, co, cn, title in scoped:
                    pieces.append(f"- [[{title}]] (conflict #{cid})")
                    pieces.append(f"  - existing: {co}")
                    pieces.append(f"  - new:      {cn}")
                pieces.append("Ask the user how to resolve, then update via /hera-conflicts.")

            n_elsewhere = conn.execute(
                "SELECT count(*) FROM conflicts "
                "WHERE status='open' AND origin_cwd IS NOT NULL AND origin_cwd != ?",
                (cwd,),
            ).fetchone()[0]
            if n_elsewhere:
                pieces.append(f"\n{n_elsewhere} open conflicts elsewhere — /hera-conflicts to review.")

            # 3) stale pending deltas
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
            n_stale = conn.execute(
                "SELECT count(*) FROM pending_deltas WHERE merged_at IS NULL AND created_at < ?",
                (cutoff,),
            ).fetchone()[0]
            if n_stale:
                pieces.append(f"\n## Stale pending deltas\n{n_stale} pages have unmerged deltas older than 24h.")
        except Exception:
            # DB might not exist yet on first launch — silent skip.
            pass

        sys.stdout.write("\n".join(pieces) + "\n")
    except Exception:
        # Silent failure — a broken SessionStart hook must not block launching.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
