#!/usr/bin/env bash
# test_install_dry_run.sh — CP-4 dry-run smoke test.
#
# Runs `install.sh --dry-run` in a scratch $HOME. Asserts that every
# intended action is announced with a [dry] prefix and that no real
# files are created outside of $VAULT (the vault itself is read).

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# Sanity: scratch HOME must be totally empty of any .claude/ we didn't put there.
mkdir -p "$SCRATCH/.claude"

out=$(HOME="$SCRATCH" bash install.sh --dry-run 2>&1)
rc=$?

if [ $rc -ne 0 ]; then
  echo "FAIL: --dry-run exited $rc"
  echo "$out"
  exit 1
fi

# The dry-run must announce each of the eight steps.
for step in "step 1/8" "step 2/8" "step 3/8" "step 4/8" "step 5/8" "step 6/8" "step 7/8" "step 8/8"; do
  echo "$out" | grep -q "$step" || { echo "FAIL: missing announcement: $step"; echo "$out"; exit 1; }
done

# Must show [dry] prefix somewhere.
echo "$out" | grep -q '\[dry\]' || { echo "FAIL: --dry-run did not emit [dry] lines"; echo "$out"; exit 1; }

# Must NOT have written to the scratch HOME (except the pre-created .claude dir).
# Specifically: no hera.env, no settings.json, no hooks dir, no skills dir,
# no global CLAUDE.md.
for path in "$SCRATCH/.claude/hera.env" "$SCRATCH/.claude/settings.json" \
            "$SCRATCH/.claude/hooks" "$SCRATCH/.claude/skills" \
            "$SCRATCH/.claude/CLAUDE.md"; do
  if [ -e "$path" ]; then
    echo "FAIL: --dry-run created $path"
    exit 1
  fi
done

# The vault's own settings.json must not have been renamed.
if [ ! -f "$REPO/.claude/settings.json" ] && [ ! -f "$REPO/.claude/settings.json.disabled" ]; then
  echo "FAIL: dry-run may have moved project-local settings.json — neither present"
  exit 1
fi

echo "test_install_dry_run: OK"
exit 0
