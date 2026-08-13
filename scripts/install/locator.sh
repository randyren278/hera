#!/usr/bin/env bash
# locator.sh — write the vault-pointer file that global hooks/skills read.
#
# Usage:
#   bash scripts/install/locator.sh <vault-dir>
#
# Writes:
#   export SECOND_BRAIN_VAULT="<absolute path>"
# to $SECOND_BRAIN_LOC_TARGET (default: ~/.claude/second-brain.env).
#
# Idempotent: the SECOND_BRAIN_VAULT line is rewritten on re-run; any other
# lines (e.g. SECOND_BRAIN_TEAM_REMOTE added by /brain-setup) are preserved.

set -u

vault="${1:-}"
if [ -z "$vault" ]; then
  echo "locator.sh: usage: locator.sh <vault-dir>" >&2
  exit 2
fi
if [ ! -d "$vault" ]; then
  echo "locator.sh: not a directory: $vault" >&2
  exit 1
fi

# Absolute path via python (portable across BSD/GNU).
abs=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$vault")

target="${SECOND_BRAIN_LOC_TARGET:-$HOME/.claude/second-brain.env}"
mkdir -p "$(dirname "$target")"

# Atomic write. Own only the SECOND_BRAIN_VAULT line + header; carry every
# other existing line through so /brain-setup's SECOND_BRAIN_TEAM_REMOTE (and
# any future addition) survives a re-install.
tmp="${target}.tmp.$$"
{
  echo "# Second Brain vault locator — written by install.sh"
  echo "# Read by global hooks in ~/.claude/hooks/ and by the brain-* skills."
  echo "export SECOND_BRAIN_VAULT=\"$abs\""
  # Preserve any lines a prior run or the brain-setup skill added. Drop our own
  # managed/header lines so they don't accumulate on re-run.
  if [ -f "$target" ]; then
    grep -vE '^[[:space:]]*(export[[:space:]]+SECOND_BRAIN_VAULT=|# Second Brain vault locator|# Read by global hooks)' "$target" || true
  fi
} > "$tmp"
mv "$tmp" "$target"
