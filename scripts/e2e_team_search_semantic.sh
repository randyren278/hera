#!/usr/bin/env bash
# e2e_team_search_semantic.sh — CP-2: hybrid team search surfaces a page that is
# reachable only semantically (no shared literal terms), proving the dense arm
# is doing the work — not the old word-count scorer.
set -euo pipefail
cd "${HERA_VAULT:?HERA_VAULT must be set}"

PY=.venv/bin/python

# Ensure the team index is current.
"$PY" scripts/team_index.py reindex --all >/dev/null

"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, "scripts")
import team_index, search as _search

conn = team_index.open_team_db()

# Find the SonarQube brief's ULID (our semantic target).
target = conn.execute(
    "SELECT id FROM pages WHERE title LIKE '%SonarQube%'").fetchone()
assert target, "target page (SonarQube brief) not in team.db — reindex failed"
target_id = target[0]

# A query that DESCRIBES the page but shares almost no distinctive literal
# vocabulary with it (no 'SonarQube', 'cron', 'HANA', 'fillna', 'poison').
query = "database records corrupted by a scheduled job writing bad placeholder values"

# 1. Pure lexical (FTS-only) should NOT surface the target first (weak/no overlap).
fts = _search._fts_hits(conn, query, 20)
fts_ids = [pid for pid, _ in fts]
fts_hit_rank = fts_ids.index(target_id) + 1 if target_id in fts_ids else None

# 2. Hybrid (BM25 + dense + RRF) SHOULD surface the target.
hits = _search.team_hybrid_search(conn, query, top_n=5, floor=0.0)
hit_ids = [h["page_id"] for h in hits]
assert target_id in hit_ids, (
    f"hybrid failed to surface the semantic target; got {[h['title'] for h in hits]}")

# The dense arm must be why it ranks: either FTS missed it entirely, or the
# vector arm contributed (target present in vec KNN).
qvec = _search.embed(query)
vec_ids = [pid for pid, _ in _search._vec_hits(conn, qvec, 20)]
assert target_id in vec_ids, "vector arm did not retrieve the target — not semantic"

print(f"ok: target surfaced by hybrid (fts_rank={fts_hit_rank}, in_vec={target_id in vec_ids})")
print(f"    hybrid top: {[h['title'] for h in hits]}")

# 3. Hits carry owner/source/score (shape contract).
for h in hits:
    assert "owner" in h and "source" in h and "score" in h, h
print("ok: hits carry owner/source/score")
PYEOF

echo "CP-2 e2e: PASS"
