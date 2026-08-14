#!/usr/bin/env bash
# e2e_inject_team.sh — CP-3: prompt_inject fuses team + local pages into ONE
# ranked block, with team lines owner-tagged.
set -euo pipefail
cd "${HERA_VAULT:?HERA_VAULT must be set}"

PY=.venv/bin/python

# Ensure the team index is current.
"$PY" scripts/team_index.py reindex --all >/dev/null

# A query that a team page answers strongly. The block must contain at least one
# owner-tagged team line, and the single-block header appears exactly once.
OUT=$(echo '{"prompt":"SonarQube cron poisoning HANA rows with literal NULL string"}' \
  | "$PY" .claude/hooks/prompt_inject.py)

echo "$OUT"

# 1. Exactly one pointer block (one header line) — proves a single fused block.
HEADERS=$(printf '%s\n' "$OUT" | grep -c 'Relevant vault pages') || true
[ "$HEADERS" -eq 1 ] || { echo "FAIL: expected 1 block header, got $HEADERS"; exit 1; }

# 2. At least one team line, owner-tagged.
printf '%s\n' "$OUT" | grep -qE '\(team: [a-z]+\)' \
  || { echo "FAIL: no owner-tagged team line in the block"; exit 1; }

# 3. The team SonarQube page is present (semantic+lexical strong match).
printf '%s\n' "$OUT" | grep -q 'SonarQube KPI Cron Fix Brief' \
  || { echo "FAIL: expected team SonarQube page in block"; exit 1; }

echo "CP-3 e2e: PASS"
