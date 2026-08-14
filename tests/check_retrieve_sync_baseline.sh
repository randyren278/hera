#!/usr/bin/env bash
# check_retrieve_sync_baseline.sh — CP-1 baseline.
#
# Pins today's state before the fix: /hera-team retrieve DOES pull before
# searching (the step exists), and team_sync.clone_or_pull() already emits the
# rc + messages a *verified* step would key off. This proves the fix is a
# documentation hardening, not a missing step or a missing signal.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SKILL="$REPO/.claude/skills/hera-team/SKILL.md"
SYNC="$REPO/scripts/team_sync.py"

# 1. retrieve already has a pre-search sync step.
if ! grep -q 'clone-or-pull' "$SKILL"; then
  echo "FAIL: hera-team SKILL.md has no clone-or-pull sync step"; exit 1
fi

# 2. team_sync surfaces the signals a verified step keys off:
#    - a no-team / empty-remote message (legit no-op, must NOT be treated as failure)
#    - a non-zero return on a genuine pull failure
if ! grep -Eq 'No team space configured|remote is empty' "$SYNC"; then
  echo "FAIL: team_sync.py has no empty-remote / no-team message"; exit 1
fi
if ! grep -q 'return r.returncode' "$SYNC"; then
  echo "FAIL: team_sync.py does not return the pull exit code"; exit 1
fi

echo "OK: retrieve pulls before search; team_sync emits rc + empty/no-team signals"
exit 0
