#!/usr/bin/env bash
# check_staging_pollution.sh — CP-1 baseline.
#
# Proves BOTH root causes exist in the live team space staging clone today:
#   (a) a stray .DS_Store is git-tracked at the staging root, and
#   (b) an orphan owner folder named after git config user.name exists whose
#       only content is .gitkeep (no code writes there — writers use HERA_OWNER),
#   while the real published pages live under the HERA_OWNER folder (randy/).
#
# Exit 0 = pollution present (known-bad baseline confirmed). Exit 1 = not the
# expected baseline (already fixed, or a different state).

set -u
: "${HERA_VAULT:?HERA_VAULT must be set}"
STAGING="$HERA_VAULT/team-staging"
GIT_OWNER="$(git -C "$STAGING" config user.name 2>/dev/null || echo)"
OWNER="${HERA_OWNER:-randy}"

if [ ! -d "$STAGING/.git" ]; then
  echo "FAIL: no staging clone at $STAGING"; exit 1
fi

# (a) .DS_Store tracked at root
if ! git -C "$STAGING" ls-files --error-unmatch .DS_Store >/dev/null 2>&1; then
  echo "not-baseline: .DS_Store is not tracked (already clean?)"; exit 1
fi
echo "confirmed: .DS_Store is tracked in staging clone"

# (b) orphan folder = git user.name, differs from HERA_OWNER, only .gitkeep
if [ -z "$GIT_OWNER" ] || [ "$GIT_OWNER" = "$OWNER" ]; then
  echo "not-baseline: git user.name ('$GIT_OWNER') absent or equals owner ('$OWNER')"; exit 1
fi
if ! git -C "$STAGING" ls-files --error-unmatch "$GIT_OWNER/.gitkeep" >/dev/null 2>&1; then
  echo "not-baseline: no orphan $GIT_OWNER/.gitkeep"; exit 1
fi
orphan_files="$(git -C "$STAGING" ls-files "$GIT_OWNER/" | grep -v '/\.gitkeep$' | grep -c . || true)"
if [ "$orphan_files" -ne 0 ]; then
  echo "not-baseline: $GIT_OWNER/ holds real files, not just .gitkeep"; exit 1
fi
echo "confirmed: orphan folder '$GIT_OWNER/' holds only .gitkeep"

# real pages live under HERA_OWNER
if ! git -C "$STAGING" ls-files "$OWNER/" | grep -q .; then
  echo "FAIL: no pages under expected owner '$OWNER/'"; exit 1
fi
echo "confirmed: real pages live under '$OWNER/'"

echo "OK: baseline pollution confirmed (.DS_Store + orphan $GIT_OWNER/)"
exit 0
