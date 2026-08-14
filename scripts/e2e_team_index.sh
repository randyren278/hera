#!/usr/bin/env bash
# e2e_team_index.sh — CP-1: team.db is built from staged Markdown, keyed by ULID.
# Verifies: reindex populates equal page/fts/vec/meta counts; a known staged
# page's ULID is present; doctor passes.
set -euo pipefail
cd "${HERA_VAULT:?HERA_VAULT must be set}"

PY=.venv/bin/python

# Build the index fresh.
"$PY" scripts/team_index.py reindex --all

# Doctor must pass (counts equal, every page vectored).
"$PY" scripts/team_index.py --doctor

# A known staged page's ULID must be present in team.db, tagged with its owner.
"$PY" - <<'PYEOF'
import os, sys, pathlib, re
sys.path.insert(0, "scripts")
import team_index
conn = team_index.open_team_db()

# Pick any staged page, read its id + owner from frontmatter, assert it's indexed.
staged = team_index._staged_pages()
assert staged, "no staged team pages found"
p = staged[0]
fm, _ = team_index._split_frontmatter(p.read_text(encoding="utf-8"))
pid = fm["id"]

row = conn.execute("SELECT id, title FROM pages WHERE id = ?", (pid,)).fetchone()
assert row, f"page {pid} not in team.db pages"
vec = conn.execute("SELECT count(*) FROM pages_vec WHERE page_id = ?", (pid,)).fetchone()[0]
assert vec == 1, f"page {pid} has {vec} vec rows"
meta = conn.execute("SELECT owner, source FROM page_meta WHERE page_id = ?", (pid,)).fetchone()
assert meta and meta[1] == "team", f"bad meta {meta}"
print(f"ok: {pid} ({row[1]}) indexed, owner={meta[0]}, vectored")
PYEOF

echo "CP-1 e2e: PASS"
