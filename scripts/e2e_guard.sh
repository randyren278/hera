#!/usr/bin/env bash
# e2e_guard.sh — refuse to wipe the operator's live vault.
#
# Two e2e scripts establish a clean state by deleting hera.db and wiki/:
# `e2e_final.sh --step setup` and `e2e_ingest.sh`. Both resolve their target
# from their own location ($0/..) and have no environment override, so running
# either one inside the installed vault destroys real notes.
#
# The locator (~/.claude/hera.env, or an exported $HERA_VAULT) names exactly one
# directory as the live vault. A scratch copy is by construction not that path,
# so guarding on the locator needs no configuration, protects the default case,
# and — unlike an opt-in flag on its own — cannot be forgotten. The flag is kept
# as the deliberate escape hatch so resetting your own vault stays possible.
#
# Source this file, then call:
#     e2e_guard_destructive "$REPO" || exit 3
# Exit 3 distinguishes "refused for safety" from usage (2) and test failure (1).

# Canonical absolute path, or empty if the path does not exist.
_e2e_realpath() {
  [ -n "${1:-}" ] && [ -d "$1" ] || return 0
  (cd "$1" 2>/dev/null && pwd -P)
}

# The vault this machine considers live: $HERA_VAULT, else the locator file.
_e2e_live_vault() {
  if [ -n "${HERA_VAULT:-}" ]; then
    printf '%s\n' "$HERA_VAULT"
    return 0
  fi
  local f="$HOME/.claude/hera.env" line
  [ -r "$f" ] || return 0
  line=$(grep -E '^[[:space:]]*(export[[:space:]]+)?HERA_VAULT=' "$f" | tail -1) || return 0
  [ -n "$line" ] || return 0
  line=${line#*HERA_VAULT=}
  line=${line%\"}; line=${line#\"}
  line=${line%\'}; line=${line#\'}
  printf '%s\n' "$line"
}

e2e_guard_destructive() {
  local target live caller
  target=$(_e2e_realpath "${1:-}")
  live=$(_e2e_realpath "$(_e2e_live_vault)")
  # The script that sourced us, for the copy-paste hint below.
  caller=$(basename "${BASH_SOURCE[1]:-${0:-e2e_final.sh}}")

  [ -n "$live" ] && [ "$target" = "$live" ] || return 0

  if [ "${HERA_E2E_ALLOW_DESTRUCTIVE:-}" = "1" ]; then
    echo "e2e_guard: HERA_E2E_ALLOW_DESTRUCTIVE=1 — wiping the LIVE vault at $target" >&2
    return 0
  fi

  cat >&2 <<EOF

REFUSING TO RUN: this step deletes hera.db and wiki/, and its target is the
live vault registered in the locator:

    $target

Run the destructive e2e steps against a scratch copy instead:

    rsync -a --exclude .git "$target/" /tmp/hera-e2e-scratch/
    bash /tmp/hera-e2e-scratch/scripts/$caller ...

To reset your own vault on purpose, re-run with HERA_E2E_ALLOW_DESTRUCTIVE=1.

EOF
  return 1
}
