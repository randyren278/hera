#!/usr/bin/env bash
# e2e_score.sh — CP-4 check for citation scoring.
#
# Preconditions: brain.db populated with pages (CP-2 ran).
# Behavior:
#   default: reset citations + session_cursors, replay tier-2 fixture,
#     assert exact tier-2 row count = 3, tier-1 rows = 0 (per R-1 verdict).
#   --replay-only: run the same session_id twice; second run must produce
#     zero new citations (cursor prevents re-scoring).

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
HOOK=".claude/hooks/stop_score.py"
FIXTURE_T2="tests/fixtures/transcript_tier2.jsonl"

MODE="${1:-default}"

if [ ! -s brain.db ] || [ ! -s "$FIXTURE_T2" ]; then
  echo "FAIL: prerequisites missing (brain.db and fixture must exist)"; exit 1
fi

reset_scorer_state() {
  $PY - <<'PY'
import sys, pathlib; sys.path.insert(0, "scripts")
import brain_db
c = brain_db.connect()
c.execute("DELETE FROM citations")
c.execute("DELETE FROM session_cursors")
c.commit()
PY
}

fire_scorer() {
  local sid="$1"
  local transcript="$2"
  printf '{"session_id":"%s","transcript_path":"%s"}' "$sid" "$transcript" \
    | $PY $HOOK
}

count_citations() {
  local tier="$1"
  $PY - <<PY
import sys, pathlib; sys.path.insert(0, "scripts")
import brain_db
c = brain_db.connect()
print(c.execute("SELECT count(*) FROM citations WHERE tier='$tier'").fetchone()[0])
PY
}

echo "== e2e_score: reset =="
reset_scorer_state
FIX_ABS="$(cd "$(dirname "$FIXTURE_T2")" && pwd)/$(basename "$FIXTURE_T2")"

if [ "$MODE" = "--replay-only" ]; then
  echo "== e2e_score: replay-only (idempotent cursor) =="
  fire_scorer "sess-replay" "$FIX_ABS" >/dev/null
  FIRST=$(count_citations final)
  fire_scorer "sess-replay" "$FIX_ABS" >/dev/null
  SECOND=$(count_citations final)
  echo "  first=$FIRST second=$SECOND"
  if [ "$FIRST" -gt 0 ] && [ "$FIRST" = "$SECOND" ]; then
    echo "no new citations"
    exit 0
  fi
  echo "FAIL: cursor did not prevent re-scoring (first=$FIRST second=$SECOND)"; exit 1
fi

echo "== e2e_score: fire tier-2 fixture =="
fire_scorer "sess-t2" "$FIX_ABS"

T2=$(count_citations final)
T1=$(count_citations thinking)
echo "  tier-2 rows: $T2"
echo "  tier-1 rows: $T1"

# Expect: 3 unique tier-2 (RAG, Hybrid Retrieval, RRF), 0 tier-1 per R-1 verdict.
if [ "$T2" -ne 3 ]; then
  echo "FAIL: expected 3 tier-2 rows, got $T2"; exit 1
fi
if [ "$T1" -ne 0 ]; then
  echo "FAIL: R-1 verdict says tier1-disabled; expected 0 tier-1 rows, got $T1"; exit 1
fi

# Cursor must have advanced.
CUR=$($PY - <<'PY'
import sys, pathlib; sys.path.insert(0, "scripts")
import brain_db
c = brain_db.connect()
print(c.execute("SELECT cursor FROM session_cursors WHERE session_id='sess-t2'").fetchone()[0])
PY
)
[ "$CUR" -ge 2 ] || { echo "FAIL: cursor did not advance (=$CUR)"; exit 1; }

# Scorer log must exist and mention session_id.
if [ ! -s .brain/scorer.log ]; then
  echo "FAIL: scorer.log not written"; exit 1
fi
grep -q "sess-t2" .brain/scorer.log || { echo "FAIL: scorer.log does not mention session"; exit 1; }

echo "== e2e_score: OK =="
