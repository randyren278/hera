"""search.py — hybrid FTS5 + sqlite-vec search with Reciprocal Rank Fusion.

Used by:
  - .claude/hooks/prompt_inject.py (per-turn context injection, ADR-04)
  - /hera-query (all three modes)

Contract:
  hybrid_search(conn, query, top_n=3, floor=0.015) -> list[Hit]
    Hit = (page_id, title, path, score, fts_rank, vec_rank)

Design:
  - BM25 rank from FTS5's built-in `bm25()` — LOWER is better (rank 1 = best)
  - vector rank from sqlite-vec KNN — LOWER is better (nearest first)
  - RRF: score(item) = Σ 1/(k + rank_i) across each source; k=60 (standard).
  - relevance floor: after RRF, drop hits whose fused score is below `floor`.
    Since RRF scores are on the order of 0.0..0.03 (2 sources, k=60), the
    default floor of 0.015 keeps only hits ranked highly by at least one
    substrate. Callers can tighten (0.02+) for injection to reduce noise.
  - trust filter (design §5.1): `trust_in` defaults to ('self','team') and is
    applied to BOTH candidate lists BEFORE fusion. Filtering after fusion
    would be a hole: an untrusted page would still occupy a rank slot, push a
    legitimate page below `top_n`, and shift every RRF score. Filtering first
    means untrusted content cannot influence the ranking it is excluded from.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from embed import embed, pack

RRF_K = 60

# The tiers a privileged session may see. `untrusted` is deliberately absent —
# a caller that wants it must name it explicitly.
DEFAULT_TRUST = ("self", "team")


@dataclass
class Hit:
    page_id: str
    title: str
    path: str
    score: float
    fts_rank: int | None
    vec_rank: int | None
    # Informational — the filter already ran. Callers use it to attribute a
    # pointer ("team: …") rather than to decide whether to show one.
    trust: str = "self"


def _fts_hits(conn: sqlite3.Connection, query: str, limit: int) -> list[tuple[str, float]]:
    """Return (page_id, bm25_rank_score) — smaller = better. rowid links pages_fts to pages via ingest bookkeeping."""
    # We store pages_fts with an implicit rowid; pages.rowid isn't directly tied
    # to pages_fts.rowid. Ingest writes into a pages_fts_map keyed on page_id.
    # For robustness, join via pages_fts_map (created lazily if missing).
    conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid   INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id)
    )""")
    # FTS5 query construction: split into alphanumeric tokens, quote each as a
    # phrase (defends against FTS5 syntax metacharacters), OR them together.
    # This gives bag-of-words semantics — matches pages containing ANY term.
    tokens = re.findall(r"\w+", query)
    tokens = [t for t in tokens if len(t) >= 2]  # drop noise
    if not tokens:
        return []
    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    sql = ("SELECT m.page_id, bm25(pages_fts) AS r "
           "FROM pages_fts JOIN pages_fts_map m ON m.rowid = pages_fts.rowid "
           "WHERE pages_fts MATCH ? ORDER BY r LIMIT ?")
    try:
        return list(conn.execute(sql, (fts_query, limit)).fetchall())
    except sqlite3.OperationalError:
        return []  # unusual query — treat as no FTS hits.


def _vec_hits(conn: sqlite3.Connection, qvec: list[float], limit: int) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT page_id, distance FROM pages_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (pack(qvec), limit),
    ).fetchall()
    return rows


def _trust_of(conn: sqlite3.Connection, page_ids) -> dict[str, str]:
    """Map page_id -> trust tier for the given ids. A page with no row, or a
    schema with no trust column, reports 'untrusted': the safe answer when we
    cannot establish provenance is to withhold, never to admit."""
    ids = list(dict.fromkeys(page_ids))
    if not ids:
        return {}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        if "trust" not in cols:
            return {pid: "untrusted" for pid in ids}
        ph = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"SELECT id, trust FROM pages WHERE id IN ({ph})", tuple(ids)).fetchall()
    except sqlite3.Error:
        return {pid: "untrusted" for pid in ids}
    found = {pid: (t or "untrusted") for pid, t in rows}
    return {pid: found.get(pid, "untrusted") for pid in ids}


def _filter_trusted(conn: sqlite3.Connection,
                    ranked: list[tuple[str, float]],
                    trust_in) -> list[tuple[str, float]]:
    """Drop disallowed-tier candidates from a ranked list BEFORE fusion, so
    they consume no rank slot and cannot perturb any surviving page's score."""
    tiers = _trust_of(conn, [pid for pid, _ in ranked])
    allowed = set(trust_in)
    return [(pid, r) for pid, r in ranked if tiers.get(pid, "untrusted") in allowed]


def _rrf_fuse(fts: list[tuple[str, float]], vec: list[tuple[str, float]]) -> list[tuple[str, float, dict]]:
    """Reciprocal Rank Fusion over two ranked substrates. Returns
    (page_id, fused_score, {fts, vec}) sorted best-first. Shared by local and
    team search so the fusion math lives in exactly one place."""
    rrf: dict[str, dict] = {}
    for rank, (pid, _r) in enumerate(fts, start=1):
        rrf.setdefault(pid, {"fts": None, "vec": None})["fts"] = rank
    for rank, (pid, _d) in enumerate(vec, start=1):
        rrf.setdefault(pid, {"fts": None, "vec": None})["vec"] = rank

    scored = []
    for pid, ranks in rrf.items():
        s = 0.0
        if ranks["fts"] is not None: s += 1.0 / (RRF_K + ranks["fts"])
        if ranks["vec"] is not None: s += 1.0 / (RRF_K + ranks["vec"])
        scored.append((pid, s, ranks))
    scored.sort(key=lambda x: -x[1])
    return scored


def hybrid_search(conn: sqlite3.Connection, query: str,
                  top_n: int = 3, floor: float = 0.015,
                  fetch: int = 20, trust_in=DEFAULT_TRUST) -> list[Hit]:
    """Return the top `top_n` hits with score ≥ floor. `fetch` controls how many
    candidates each substrate contributes before fusion.

    `trust_in` is the set of tiers a hit may carry; it defaults to
    ('self','team') so untrusted pages are excluded unless a caller asks for
    them by name. The filter runs on each substrate's candidates BEFORE RRF,
    so an untrusted page consumes no rank slot (design §5.1).
    """
    if not tuple(trust_in):
        return []  # no tier permitted → nothing may be returned.
    qvec = embed(query)
    fts = _filter_trusted(conn, _fts_hits(conn, query, fetch), trust_in)
    vec = _filter_trusted(conn, _vec_hits(conn, qvec, fetch), trust_in)
    scored = _rrf_fuse(fts, vec)

    hits: list[Hit] = []
    for pid, s, ranks in scored[:top_n]:
        if s < floor:
            continue
        row = conn.execute(
            "SELECT title, path, trust FROM pages "
            "WHERE id = ? AND archived_at IS NULL AND trust IN "
            "(" + ",".join(["?"] * len(tuple(trust_in))) + ")",
            (pid, *tuple(trust_in)),
        ).fetchone()
        if not row:
            continue  # archived, missing, or (belt-and-braces) wrong tier.
        title, path, tier = row
        hits.append(Hit(page_id=pid, title=title, path=path, score=s,
                        fts_rank=ranks["fts"], vec_rank=ranks["vec"],
                        trust=tier))
    return hits


def team_hybrid_search(conn: sqlite3.Connection, query: str,
                       top_n: int = 3, floor: float = 0.015,
                       fetch: int = 20, owner: str | None = None,
                       trust_in=DEFAULT_TRUST) -> list[dict]:
    """Hybrid search over a team.db connection. Same BM25+dense+RRF substrate as
    hybrid_search, but joins page_meta to attach owner/source and returns dicts
    (team hits carry owner, which Hit does not). When `owner` is set, restrict to
    that teammate's pages.

    `trust_in` works as it does in hybrid_search, but the store — not the row —
    is the tier authority here: ADR-14 says team.db indexes published team
    pages and nothing else, so the whole store is 'team'. Hence the gate is on
    'team' being permitted, and the per-row pass only drops anything explicitly
    marked 'untrusted' (which should be impossible, and is worth failing shut
    on if it ever happens). Rows left at the column's 'self' default by an
    older build are still team content and are kept.
    """
    if "team" not in tuple(trust_in):
        return []
    _NOT_UNTRUSTED = ("self", "team")
    qvec = embed(query)
    fts = _filter_trusted(conn, _fts_hits(conn, query, fetch), _NOT_UNTRUSTED)
    vec = _filter_trusted(conn, _vec_hits(conn, qvec, fetch), _NOT_UNTRUSTED)
    scored = _rrf_fuse(fts, vec)

    out: list[dict] = []
    for pid, s, _ranks in scored:
        if s < floor:
            continue
        row = conn.execute(
            "SELECT p.title, p.path, m.owner, m.source "
            "FROM pages p JOIN page_meta m ON m.page_id = p.id "
            "WHERE p.id = ? AND p.archived_at IS NULL",
            (pid,),
        ).fetchone()
        if not row:
            continue
        title, path, own, source = row
        if owner and own != owner:
            continue
        out.append({"page_id": pid, "title": title, "path": path,
                    "owner": own, "source": source, "score": s})
        if len(out) >= top_n:
            break
    return out
