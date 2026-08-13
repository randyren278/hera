#!/usr/bin/env bash
# check_gitignore_blocks_junk.sh — CP-2 check.
#
# Hermetic proof that the staging .gitignore team_sync.py ships actually keeps
# a stray .DS_Store out of `git add -A` — the exact call publish.py and
# team_remove.py make in the staging clone. Uses a scratch repo; never touches
# the live team brain.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# Point team_sync at the scratch dir as its staging clone, with no remote so
# clone_or_pull's remote branch is skipped — we only exercise the ignore-file
# writer against a fresh git repo.
export SECOND_BRAIN_VAULT="$SCRATCH/vault"
mkdir -p "$SECOND_BRAIN_VAULT/team-brain-staging"
git -C "$SECOND_BRAIN_VAULT/team-brain-staging" init -q

# Invoke the real helper the engine relies on.
python3 - <<PY
import os, sys, pathlib
sys.path.insert(0, "$REPO/scripts")
import team_sync
team_sync._ensure_staging_gitignore()
PY

S="$SECOND_BRAIN_VAULT/team-brain-staging"
if [ ! -f "$S/.gitignore" ]; then
  echo "FAIL: team_sync did not write .gitignore"; exit 1
fi
if ! grep -q '.DS_Store' "$S/.gitignore"; then
  echo "FAIL: .gitignore does not list .DS_Store"; exit 1
fi

# Drop OS junk and a real page, then do exactly what the engines do.
: > "$S/.DS_Store"
mkdir -p "$S/randy/sources"
echo "# real page" > "$S/randy/sources/page.md"
git -C "$S" add -A

if git -C "$S" diff --cached --name-only | grep -q '.DS_Store'; then
  echo "FAIL: .DS_Store was staged despite .gitignore"
  git -C "$S" diff --cached --name-only
  exit 1
fi
if ! git -C "$S" diff --cached --name-only | grep -q 'randy/sources/page.md'; then
  echo "FAIL: real page was not staged"; exit 1
fi

echo "OK: .gitignore blocks .DS_Store from git add -A; real pages still staged"
exit 0
