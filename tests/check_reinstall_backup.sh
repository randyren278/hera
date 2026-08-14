#!/usr/bin/env bash
# check_reinstall_backup.sh — CP-1 check.
#
# Reproduces the re-install path and asserts that after install → install,
# at least one settings.json.hera-backup.* is free of Hera hooks
# (i.e. the clean pre-install backup was preserved, not clobbered).

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

mkdir -p "$SCRATCH/.claude"
# Seed a pristine settings.json with an unrelated hook (no Hera refs).
cat > "$SCRATCH/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "echo users-own-hook" } ] }
    ]
  }
}
JSON

HOME="$SCRATCH" bash install.sh > /dev/null 2>&1
# Ensure distinct backup timestamps if a second backup were ever taken.
sleep 1
HOME="$SCRATCH" bash install.sh > /dev/null 2>&1

clean_found=0
for b in "$SCRATCH"/.claude/settings.json.hera-backup.*; do
  [ -e "$b" ] || continue
  if ! grep -q "hera.env" "$b"; then
    clean_found=1
    echo "clean backup preserved: $(basename "$b")"
  fi
done

if [ "$clean_found" -ne 1 ]; then
  echo "FAIL: no clean settings.json backup survived a re-install"
  ls -la "$SCRATCH"/.claude/settings.json.hera-backup.* 2>/dev/null
  exit 1
fi

echo "OK: re-install preserved a clean backup"
exit 0
