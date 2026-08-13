#!/usr/bin/env bash
# check_polluted_backup_uninstall.sh — CP-2 check.
#
# Simulates a machine that re-installed BEFORE this fix (so its newest
# settings.json.brain-backup.* already contains second-brain hooks), then runs
# a single uninstall and asserts the resulting settings.json is free of
# second-brain hooks while the user's own unrelated hook survives.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

mkdir -p "$SCRATCH/.claude"
# User's pristine settings with an unrelated hook.
cat > "$SCRATCH/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "echo users-own-hook" } ] }
    ]
  }
}
JSON

# Install once (creates the clean pre-install backup + merges our hooks).
HOME="$SCRATCH" bash install.sh > /dev/null 2>&1

# Forge a POLLUTED backup that is newer than the clean one: copy the current
# (merged) settings.json to a backup with a later timestamp. This reproduces
# the pre-fix state where re-install clobbered the newest backup.
sleep 1
POLLUTED="$SCRATCH/.claude/settings.json.brain-backup.$(date +%Y%m%d-%H%M%S)"
cp -p "$SCRATCH/.claude/settings.json" "$POLLUTED"
if ! grep -q "second-brain.env" "$POLLUTED"; then
  echo "SETUP FAIL: forged backup is not polluted"; exit 1
fi

# Single uninstall.
HOME="$SCRATCH" bash install.sh --uninstall > /dev/null 2>&1

S="$SCRATCH/.claude/settings.json"
if [ ! -f "$S" ]; then
  echo "FAIL: settings.json was removed, but user's PreToolUse hook should have kept it"
  exit 1
fi

if grep -q "second-brain.env" "$S"; then
  echo "FAIL: second-brain hooks survived uninstall (polluted backup was restored uncleaned)"
  grep -n "second-brain.env" "$S"
  exit 1
fi

if ! grep -q "users-own-hook" "$S"; then
  echo "FAIL: user's own PreToolUse hook was lost"
  exit 1
fi

echo "OK: uninstall cleaned a polluted backup, preserved user hooks"
exit 0
