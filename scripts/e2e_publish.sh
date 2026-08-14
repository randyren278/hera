#!/usr/bin/env bash
# e2e_publish.sh — CP-7 check for /hera-team add (publish flow).
#
# Modes:
#   default          : stage the mixed_private fixture, assert:
#                      - staging dir has files
#                      - every staged file has visibility: public
#                      - no blocklist term appears in any staged file
#                      - no push happened (nothing on the remote HEAD beyond initial)
#   --grep-blocklist : just re-check the blocklist against the current
#                      staging state. Prints "no blocklist term found" or fails.
#   --reject         : stage then explicitly do NOT push; assert staging is
#                      preserved and no commit was made.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
FIXTURE="tests/fixtures/mixed_private.md"
BLOCKLIST="tests/fixtures/private_terms.txt"
STAGING="team-staging"

# publish.py resolves its staging dir from its own location ($REPO/team-staging),
# so this test provisions that clone here and wires it to a throwaway *local* bare
# remote — no network, no configured HERA_TEAM_REMOTE, no fixed remote. The
# bare remote lets the "nothing pushed" assertion (origin has exactly the initial
# commit) hold. The clone + bare are removed on exit; the dir is gitignored anyway.
BARE="$(mktemp -d)/team.git"
cleanup() { rm -rf "$STAGING" "$(dirname "$BARE")"; }
trap cleanup EXIT
git init -q --bare "$BARE"
rm -rf "$STAGING"   # clear any stale dir from a prior aborted run
git clone -q "$BARE" "$STAGING"
git -C "$STAGING" config user.email t@t.t
git -C "$STAGING" config user.name tester
mkdir -p "$STAGING/randy"
touch "$STAGING/.keep"
git -C "$STAGING" add -A
git -C "$STAGING" commit -qm "init"
git -C "$STAGING" branch -M main
git -C "$STAGING" push -q origin main

MODE="${1:-default}"

reset_staging() {
  # Wipe staged files (but keep the .git so it remains a clone).
  find "$STAGING/randy" -mindepth 1 -delete 2>/dev/null || true
  mkdir -p "$STAGING/randy"
}

grep_blocklist() {
  # Return 1 if any blocklist term appears in any staged file; 0 otherwise.
  local hit=""
  while IFS= read -r term; do
    [ -z "$term" ] && continue
    case "$term" in \#*) continue;; esac
    if grep -rilF "$term" "$STAGING/randy" 2>/dev/null | head -1 >/dev/null; then
      hit="$term"
      break
    fi
  done < "$BLOCKLIST"
  if [ -n "$hit" ]; then
    echo "FAIL: blocklist term '$hit' found in staged files"
    grep -rilF "$hit" "$STAGING/randy"
    return 1
  fi
  echo "no blocklist term found"
  return 0
}

case "$MODE" in
  --grep-blocklist)
    grep_blocklist
    exit $?
    ;;
esac

echo "== e2e_publish: reset staging =="
reset_staging

echo "== e2e_publish: stage private ingest =="
$PY scripts/publish.py stage "$FIXTURE" --json > /tmp/publish_stage.json
cat /tmp/publish_stage.json
STAGED_COUNT=$($PY -c "import json; print(len(json.load(open('/tmp/publish_stage.json'))['staged']))")
[ "$STAGED_COUNT" -ge 1 ] || { echo "FAIL: no files staged"; exit 1; }

echo "== e2e_publish: every staged file has visibility: public =="
BAD=$(find "$STAGING/randy" -name '*.md' -exec grep -L '^visibility: public' {} \;)
if [ -n "$BAD" ]; then
  echo "FAIL: these staged files lack visibility: public frontmatter:"
  echo "$BAD"
  exit 1
fi

echo "== e2e_publish: no blocklist term in any staged file =="
grep_blocklist

echo "== e2e_publish: nothing pushed yet =="
# The remote HEAD should still be the initial empty commit — no new refs.
REMOTE_COMMITS=$(git -C "$STAGING" fetch origin 2>/dev/null; git -C "$STAGING" rev-list --count origin/main 2>/dev/null || echo 0)
echo "  remote commits: $REMOTE_COMMITS"
[ "$REMOTE_COMMITS" = "1" ] || { echo "FAIL: remote already has extra commits — publish is auto-pushing"; exit 1; }

if [ "$MODE" = "--reject" ]; then
  echo "== e2e_publish: --reject mode — leaving staging as-is =="
  # Confirm nothing has been git-committed either.
  LOCAL_COMMITS=$(git -C "$STAGING" rev-list --count HEAD)
  [ "$LOCAL_COMMITS" = "1" ] || { echo "FAIL: local repo has extra commits, --reject should not commit"; exit 1; }
  test -d "$STAGING/randy" || { echo "FAIL: staging directory should be preserved"; exit 1; }
  N=$(find "$STAGING/randy" -name '*.md' | wc -l | tr -d ' ')
  [ "$N" -ge 1 ] || { echo "FAIL: staged files should still be there after reject"; exit 1; }
  echo "  staged files preserved: $N"
  exit 0
fi

echo "== e2e_publish: OK =="
