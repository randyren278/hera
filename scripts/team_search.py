#!/usr/bin/env python3
"""team_search.py — hybrid retrieval across the team space + personal vault.

Team pages are indexed in team.db (see team_index.py) with the SAME
BM25 + dense-vector + RRF substrate as the personal hera.db. This engine
runs that hybrid search over team.db and — unless scoped to a specific
teammate — also runs the local hybrid_search over hera.db, then merges the
two ranked lists by fused RRF score (both on the same scale) and tags each hit
with its owner and source (team|personal).

Attribution: team hits carry `owner` from team.db's page_meta (stamped at
index time from the `owner:` frontmatter / <owner>/ folder). Personal hits are
owner "me", source "personal".

CLI:
  team_search.py [--owner NAME] [--top N] [--json] "<query>"

  --owner NAME  restrict to that teammate's pages; personal vault is excluded
                (you asked for *their* view). Personal is included otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

_env_vault = os.environ.get("HERA_VAULT")
REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import search as _search  # noqa: E402
import team_index  # noqa: E402


def search(query: str, owner: str | None = None, top: int = 10,
           floor: float = 0.015) -> list[dict]:
    if not query.strip():
        return []

    hits: list[dict] = []

    # Team side: hybrid over team.db. Fetch `top` from the team substrate.
    try:
        tconn = team_index.open_team_db()
        hits += _search.team_hybrid_search(tconn, query, top_n=top, floor=floor,
                                            owner=owner)
    except Exception:
        pass  # no team.db / Ollama down → team contributes nothing

    # Personal side: only when not scoping to a specific teammate.
    if not owner:
        try:
            conn = hera_db.connect()
            for h in _search.hybrid_search(conn, query, top_n=top, floor=floor):
                hits.append({"page_id": h.page_id, "title": h.title,
                             "path": h.path, "owner": "me",
                             "source": "personal", "score": h.score})
        except Exception:
            pass

    # Merge by fused RRF score (team and personal use the same scale).
    hits.sort(key=lambda h: (-h["score"], h["title"]))
    return hits[:top]


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--owner", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results = search(a.query, owner=a.owner, top=a.top)
    if a.json:
        print(json.dumps(results, indent=2))
        return 0

    if not results:
        print("no matches.")
        return 0
    by_owner: dict[str, list[dict]] = {}
    for h in results:
        by_owner.setdefault(h["owner"], []).append(h)
    for own, hs in by_owner.items():
        label = "you" if own == "me" else own
        print(f"\n{label} ({hs[0]['source']}):")
        for h in hs:
            print(f"  [{h['score']:.4f}] {h['title']}  ({h['path']})")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
