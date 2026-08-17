#!/usr/bin/env bash
# e2e_final.sh — CP-FINAL orchestrator (clean-state end-to-end).
#
# Each --step reproduces one of the phase checks against the shared repo state.
# The `setup` step wipes hera.db and wiki/ (keeps .claude/, scripts/, tests/,
# team-staging/) so downstream steps run from a clean baseline.
#
# Usage: bash scripts/e2e_final.sh --step <setup|ingest|inject|score|conflicts|filing|publish|prune>
#        bash scripts/e2e_final.sh --all      # every step, in dependency order
#
# `setup` deletes hera.db and wiki/. It refuses to run against the vault named
# in the locator — see scripts/e2e_guard.sh. Run the suite from a scratch copy.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
. "$REPO/scripts/e2e_guard.sh"

STEPS="setup ingest inject score conflicts filing publish prune"

usage() {
  echo "usage: e2e_final.sh --step <$(echo "$STEPS" | tr ' ' '|')>"
  echo "       e2e_final.sh --all"
}

STEP=""
ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --step) STEP="${2:-}"; shift 2 ;;
    --all) ALL=1; shift ;;
    *) echo "unknown flag: $1"; usage; exit 2 ;;
  esac
done

# --all runs every step in this same script, in dependency order. A bare
# invocation stays a usage error on purpose: the first step is destructive, so
# running the whole suite must be something the caller asks for by name.
if [ "$ALL" = "1" ]; then
  [ -z "$STEP" ] || { echo "--all and --step are mutually exclusive"; exit 2; }
  for s in $STEPS; do
    bash "$0" --step "$s"
  done
  echo "== CP-FINAL::all OK =="
  exit 0
fi

[ -n "$STEP" ] || { usage; exit 2; }

case "$STEP" in
  setup)
    echo "== CP-FINAL::setup =="
    e2e_guard_destructive "$REPO" || exit 3
    # Wipe hera.db, wiki/, .hera/, but keep .claude/, scripts/, tests/, team-staging/, .venv/.
    rm -f hera.db hera.db-wal hera.db-shm
    rm -rf wiki .hera
    mkdir -p wiki
    bash scripts/preflight.sh
    $PY scripts/hera_db.py --init
    $PY scripts/hera_db.py --doctor
    echo "== CP-FINAL::setup OK =="
    exit 0
    ;;

  ingest)
    echo "== CP-FINAL::ingest =="
    bash scripts/e2e_ingest.sh
    ;;

  inject)
    echo "== CP-FINAL::inject =="
    bash scripts/e2e_inject.sh
    bash scripts/e2e_inject.sh --no-ollama
    ;;

  score)
    echo "== CP-FINAL::score =="
    bash scripts/e2e_score.sh
    bash scripts/e2e_score.sh --replay-only
    ;;

  conflicts)
    echo "== CP-FINAL::conflicts =="
    bash scripts/e2e_conflicts.sh
    bash scripts/e2e_conflicts.sh --channel 3 >/dev/null
    bash scripts/e2e_conflicts.sh --resolve resolved_new >/dev/null
    ;;

  filing)
    echo "== CP-FINAL::filing =="
    bash scripts/e2e_filing.sh
    bash scripts/e2e_filing.sh --twice
    bash scripts/e2e_filing.sh --explicit
    bash scripts/e2e_filing.sh --implication
    ;;

  publish)
    echo "== CP-FINAL::publish =="
    bash scripts/e2e_publish.sh
    bash scripts/e2e_publish.sh --grep-blocklist
    bash scripts/e2e_publish.sh --reject
    ;;

  prune)
    echo "== CP-FINAL::prune =="
    bash scripts/e2e_prune.sh
    bash scripts/e2e_prune.sh --grep-sources
    bash scripts/e2e_prune.sh --young-page
    bash scripts/e2e_prune.sh --restore
    ;;

  *)
    echo "unknown step: $STEP"; exit 2
    ;;
esac

echo "== CP-FINAL::$STEP OK =="
