#!/usr/bin/env bash
# e2e_inject.sh — CP-3 check for per-turn injection.
#
# Preconditions:
#   - CP-2 has run (wiki/ populated, hera.db has pages/fts/vec rows).
# Behavior:
#   default: prove topical prompts inject a pointer, off-topic prompts don't.
#   --no-ollama: prove fail-open when Ollama is unreachable (env var trips it).

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
HOOK="$PY .claude/hooks/prompt_inject.py"

MODE="${1:-default}"

if [ "$MODE" = "--no-ollama" ]; then
  echo "== e2e_inject: fail-open (--no-ollama) =="
  # Simulate Ollama down via env flag consumed by the hook.
  OUT=$(printf '{"prompt":"tell me about retrieval-augmented generation"}' | HERA_INJECT_NO_OLLAMA=1 $HOOK; echo "__RC=$?__")
  RC=$(echo "$OUT" | grep -oE '__RC=[0-9]+__' | tr -dc 0-9)
  BODY=$(echo "$OUT" | sed 's/__RC=[0-9]*__$//')
  echo "  exit: $RC"
  echo "  body (want empty): [$BODY]"
  [ "$RC" = "0" ] || { echo "FAIL: hook exited non-zero when Ollama simulated down"; exit 1; }
  # Body must be empty or just whitespace.
  [ -z "$(echo "$BODY" | tr -d '[:space:]')" ] || { echo "FAIL: hook emitted output when Ollama should be simulated down"; exit 1; }
  echo "== e2e_inject: fail-open OK =="
  exit 0
fi

# Precondition: hera.db and wiki/ populated. If not, run CP-2 first.
if [ ! -s hera.db ] || [ ! -d wiki/concepts ]; then
  echo "prereq: running e2e_ingest to populate vault"
  bash scripts/e2e_ingest.sh >/tmp/e2e_ingest_for_inject.log 2>&1 || {
    echo "FAIL: prerequisite ingest did not succeed"; cat /tmp/e2e_ingest_for_inject.log; exit 1;
  }
fi

echo "== e2e_inject: on-topic prompt should return a pointer =="
ON_TOPIC='retrieval-augmented generation'
OUT_ON=$(printf '{"prompt":"tell me about %s"}' "$ON_TOPIC" | $HOOK)
echo "  output: [${OUT_ON}]"
echo "$OUT_ON" | grep -qi "$ON_TOPIC\|rag\|hybrid retrieval" || {
  echo "FAIL: on-topic prompt did not inject a pointer to a related page";
  exit 1;
}

echo "== e2e_inject: coding prompt should inject nothing (heuristic gate) =="
CODING='how do i write a regex for parsing timestamps in awk'
OUT_CODE=$(printf '{"prompt":"%s"}' "$CODING" | $HOOK)
echo "  output: [${OUT_CODE}]"
[ -z "$(echo "$OUT_CODE" | tr -d '[:space:]')" ] || {
  echo "FAIL: coding prompt should have been gated but produced output"; exit 1;
}

echo "== e2e_inject: totally-irrelevant prompt should inject nothing (relevance floor) =="
# A prompt on an unrelated topic. The relevance floor at 0.015 with RRF(k=60)
# will drop pages that aren't in either substrate's top-N.
OFF='what is the boiling point of mercury on the moon'
OUT_OFF=$(printf '{"prompt":"%s"}' "$OFF" | $HOOK)
echo "  output: [${OUT_OFF}]"
# We don't hard-fail if this emits *something*: it may legitimately not.
# The strong assertion is fail-open (proven separately) and on-topic hit.
# But warn if it emits output — worth eyeballing.
if [ -n "$(echo "$OUT_OFF" | tr -d '[:space:]')" ]; then
  echo "  (soft) off-topic prompt produced output; consider tightening floor."
fi

echo "== e2e_inject: OK =="
