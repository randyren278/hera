#!/usr/bin/env bash
# test_uninstall_roundtrip.sh — CP-5 test.
#
# Installs into a scratch $HOME, uninstalls, and asserts:
#   - hooks + skills symlinks removed
#   - locator removed
#   - settings.json restored to its pre-install state (or removed if no
#     backup existed)
#   - re-installing afterwards returns the scratch $HOME to a valid
#     installed state.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

pass=0; fail=0
report() {
  local name="$1" rc="$2"
  if [ "$rc" -eq 0 ]; then
    echo "  ok    $name"
    pass=$((pass + 1))
  else
    echo "  FAIL  $name"
    fail=$((fail + 1))
  fi
}

# --- Case A: no pre-existing settings.json ---------------------------------
mkdir -p "$SCRATCH/.claude"
HOME="$SCRATCH" bash install.sh > /dev/null

test -f "$SCRATCH/.claude/hera.env"; report "install: locator present" $?
test -L "$SCRATCH/.claude/hooks/session_start.py"; report "install: hooks symlinked" $?
test -L "$SCRATCH/.claude/skills/hera-ingest"; report "install: skills symlinked" $?
test -f "$SCRATCH/.claude/settings.json"; report "install: settings.json created" $?

HOME="$SCRATCH" bash install.sh --uninstall > /dev/null

test ! -e "$SCRATCH/.claude/hera.env"; report "uninstall: locator removed" $?
test ! -e "$SCRATCH/.claude/hooks/session_start.py"; report "uninstall: hook symlink removed" $?
test ! -e "$SCRATCH/.claude/skills/hera-ingest"; report "uninstall: skill symlink removed" $?
# No pre-install backup → settings.json should be removed (was ours only).
test ! -e "$SCRATCH/.claude/settings.json"; report "uninstall: settings.json removed (no prior backup)" $?

# Re-install must work.
HOME="$SCRATCH" bash install.sh > /dev/null
test -L "$SCRATCH/.claude/hooks/session_start.py"; report "reinstall: hooks re-symlinked" $?

# --- Case B: pre-existing unrelated hook --------------------------------
rm -rf "$SCRATCH/.claude"
mkdir -p "$SCRATCH/.claude"
cat > "$SCRATCH/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "echo preserved" } ] }
    ]
  }
}
JSON

HOME="$SCRATCH" bash install.sh > /dev/null
python3 -c "
import json
d = json.load(open('$SCRATCH/.claude/settings.json'))
assert 'PreToolUse' in d['hooks']
assert 'SessionStart' in d['hooks']
"
report "install: preserves unrelated hooks + adds ours" $?

HOME="$SCRATCH" bash install.sh --uninstall > /dev/null
python3 -c "
import json
d = json.load(open('$SCRATCH/.claude/settings.json'))
assert 'PreToolUse' in d['hooks'], 'PreToolUse lost after uninstall'
"
report "uninstall: preserves unrelated hooks after restore" $?

# --- Case C: re-install then uninstall (polluted-backup regression) ------
# Install twice, uninstall once. Before the skip-backup fix, the second
# install clobbered the newest backup with a merged (polluted) copy, and
# uninstall restored it — leaving all four Hera hooks behind.
rm -rf "$SCRATCH/.claude"
mkdir -p "$SCRATCH/.claude"
cat > "$SCRATCH/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "echo preserved-c" } ] }
    ]
  }
}
JSON

HOME="$SCRATCH" bash install.sh > /dev/null
sleep 1
HOME="$SCRATCH" bash install.sh > /dev/null
HOME="$SCRATCH" bash install.sh --uninstall > /dev/null

test -f "$SCRATCH/.claude/settings.json"; \
  report "uninstall(2x): settings.json kept (user hook present)" $?
if grep -q "hera.env" "$SCRATCH/.claude/settings.json" 2>/dev/null; then
  report "uninstall(2x): no Hera hooks remain" 1
else
  report "uninstall(2x): no Hera hooks remain" 0
fi
grep -q "preserved-c" "$SCRATCH/.claude/settings.json" 2>/dev/null; \
  report "uninstall(2x): preserves user's PreToolUse hook" $?

# --- Case D: re-install preserves the team remote (regression) -----------
# The locator rewrites hera.env on every install. Before the fix it
# rewrote the file from scratch, wiping the HERA_TEAM_REMOTE line that
# /hera-setup appends. A re-install must keep it; only uninstall removes it.
rm -rf "$SCRATCH/.claude"
mkdir -p "$SCRATCH/.claude"

HOME="$SCRATCH" bash install.sh > /dev/null
# Simulate /hera-setup persisting the remote beside the vault line.
echo 'export HERA_TEAM_REMOTE="https://example.test/team.git"' \
  >> "$SCRATCH/.claude/hera.env"

HOME="$SCRATCH" bash install.sh > /dev/null

grep -q 'HERA_TEAM_REMOTE=.*example.test' "$SCRATCH/.claude/hera.env"; \
  report "reinstall: team remote preserved" $?
[ "$(grep -c '^export HERA_VAULT=' "$SCRATCH/.claude/hera.env")" -eq 1 ]; \
  report "reinstall: vault line not duplicated" $?

HOME="$SCRATCH" bash install.sh --uninstall > /dev/null
test ! -e "$SCRATCH/.claude/hera.env"; \
  report "uninstall: env (incl. remote) removed" $?

echo
echo "results: $pass passed, $fail failed"
if [ $fail -ne 0 ]; then
  exit 1
fi
exit 0
