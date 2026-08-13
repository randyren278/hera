#!/usr/bin/env bash
# check_team_remote.sh — exercise team_sync.py against a local bare repo.
#
# Proves the four remote states without any network/SSO dependency:
#   (a) unset remote        -> clean no-op, exit 0, no staging/ created
#   (b) check <reachable>   -> exit 0 (incl. reachable-but-empty repo)
#   (c) check <nonexistent> -> non-zero
#   (d) clone-or-pull       -> clones, second run ff-pulls, both exit 0
#
# Self-contained: builds throwaway dirs under a temp root, cleans up on exit.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC="$SCRIPT_DIR/team_sync.py"
PY="${PYTHON:-python}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $1"; exit 1; }

# A throwaway vault so team_sync.py's STAGING lands under $TMP, never the real vault.
VAULT="$TMP/vault"
mkdir -p "$VAULT"
STAGING="$VAULT/team-brain-staging"

# A reachable local bare repo with one commit on main.
BARE="$TMP/team.git"
git init --quiet --bare "$BARE"
SEED="$TMP/seed"
git init --quiet "$SEED"
git -C "$SEED" config user.email t@e.st
git -C "$SEED" config user.name tester
echo "# team brain" > "$SEED/README.md"
git -C "$SEED" add -A
git -C "$SEED" commit --quiet -m "seed"
git -C "$SEED" branch -M main
git -C "$SEED" remote add origin "$BARE"
git -C "$SEED" push --quiet origin main

NONEXISTENT="$TMP/does-not-exist.git"

run_sync() {  # run_sync <remote-or-empty> <args...>
  local remote="$1"; shift
  if [ -z "$remote" ]; then
    env -u SECOND_BRAIN_TEAM_REMOTE SECOND_BRAIN_VAULT="$VAULT" "$PY" "$SYNC" "$@"
  else
    SECOND_BRAIN_TEAM_REMOTE="$remote" SECOND_BRAIN_VAULT="$VAULT" "$PY" "$SYNC" "$@"
  fi
}

# --- (a) unset remote: clean no-op ---
# Also blank HOME so a real ~/.claude/second-brain.env can't leak a remote in.
OUT="$(env -u SECOND_BRAIN_TEAM_REMOTE HOME="$TMP" SECOND_BRAIN_VAULT="$VAULT" "$PY" "$SYNC" clone-or-pull 2>&1)"
RC=$?
[ $RC -eq 0 ] || fail "(a) unset remote exited $RC, expected 0"
echo "$OUT" | grep -q "No team space configured" || fail "(a) missing no-team message: $OUT"
[ ! -e "$STAGING" ] || fail "(a) staging created despite no configured remote"
echo "PASS (a) unset remote -> clean no-op"

# --- (b) check reachable ---
run_sync "$BARE" check "$BARE" >/dev/null 2>&1 || fail "(b) check <reachable bare> exited non-zero"
echo "PASS (b) check reachable -> exit 0"

# --- (c) check nonexistent ---
if run_sync "" check "$NONEXISTENT" >/dev/null 2>&1; then
  fail "(c) check <nonexistent> exited 0, expected non-zero"
fi
echo "PASS (c) check nonexistent -> non-zero"

# --- (d) clone then ff-pull ---
run_sync "$BARE" clone-or-pull >/dev/null 2>&1 || fail "(d) first clone-or-pull exited non-zero"
[ -e "$STAGING/.git" ] || fail "(d) staging not cloned"
run_sync "$BARE" clone-or-pull >/dev/null 2>&1 || fail "(d) second clone-or-pull (ff-pull) exited non-zero"
echo "PASS (d) clone then ff-pull -> both exit 0"

echo "ALL PASS"
