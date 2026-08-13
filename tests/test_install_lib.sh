#!/usr/bin/env bash
# test_install_lib.sh — CP-1 tests for scripts/install/lib.sh.
#
# Covers: backup+restore roundtrip; merge with empty target; merge with
# a target that already has an unrelated hook; merge run twice → no
# duplicate entries.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO/scripts/install/lib.sh"

pass=0
fail=0
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

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

# --- Test 1: backup on missing file is a no-op with rc=0 -------------------
out=$(backup_file "$scratch/nope.txt")
rc=$?
[ $rc -eq 0 ] && [ -z "$out" ]
report "backup_file: missing source is no-op rc=0" $?

# --- Test 2: backup + restore roundtrip -------------------------------------
echo "original" > "$scratch/settings.json"
backup_path=$(backup_file "$scratch/settings.json")
[ -f "$backup_path" ] && grep -q original "$backup_path"
report "backup_file: creates backup file" $?

echo "modified" > "$scratch/settings.json"
restore_latest_backup "$scratch/settings.json"
grep -q original "$scratch/settings.json"
report "restore_latest_backup: restores content" $?

# --- Test 3: restore with no backup -----------------------------------------
rm -f "$scratch"/other.json.brain-backup.* 2>/dev/null
echo "x" > "$scratch/other.json"
restore_latest_backup "$scratch/other.json"
[ $? -eq 1 ]
report "restore_latest_backup: rc=1 when no backup exists" $?

# --- Test 4: merge into empty (missing) target ------------------------------
cat > "$scratch/frag.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "echo ss" } ] }
    ]
  }
}
JSON
rm -f "$scratch/settings.json"
merge_settings_json "$scratch/settings.json" "$scratch/frag.json"
python3 -c "
import json
d = json.load(open('$scratch/settings.json'))
assert 'SessionStart' in d['hooks']
assert d['hooks']['SessionStart'][0]['hooks'][0]['command'] == 'echo ss'
"
report "merge_settings_json: creates from empty target" $?

# --- Test 5: merge into target with unrelated hook preserves it -------------
cat > "$scratch/settings2.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "echo unrelated" } ] }
    ]
  }
}
JSON
merge_settings_json "$scratch/settings2.json" "$scratch/frag.json"
python3 -c "
import json
d = json.load(open('$scratch/settings2.json'))
cmds = [h['command'] for entry in d['hooks']['PreToolUse'] for h in entry['hooks']]
assert 'echo unrelated' in cmds, 'PreToolUse lost'
cmds2 = [h['command'] for entry in d['hooks']['SessionStart'] for h in entry['hooks']]
assert 'echo ss' in cmds2, 'SessionStart not added'
"
report "merge_settings_json: preserves unrelated hooks" $?

# --- Test 6: merge run twice does not duplicate -----------------------------
merge_settings_json "$scratch/settings.json" "$scratch/frag.json"
merge_settings_json "$scratch/settings.json" "$scratch/frag.json"
python3 -c "
import json
d = json.load(open('$scratch/settings.json'))
groups = d['hooks']['SessionStart']
sigs = [tuple(h.get('command') for h in g.get('hooks', [])) for g in groups]
assert len(sigs) == len(set(sigs)), f'duplicates: {sigs}'
assert len(sigs) == 1, f'expected 1 group, got {len(sigs)}'
"
report "merge_settings_json: idempotent (no duplicates)" $?

# --- Test 7: require_env is truthful ----------------------------------------
require_env
report "require_env: passes on a working host" $?

# --- Test 8: append to empty/missing target creates one block ---------------
mdscratch="$scratch/mdcase"
mkdir -p "$mdscratch"
printf 'line1\nline2\n' > "$mdscratch/vault_claude.md"
gmd="$mdscratch/global_claude.md"
append_global_claudemd "$gmd" "$mdscratch/vault_claude.md"
python3 -c "
t = open('$gmd').read()
assert t.count('>>> second-brain') == 1, 'not exactly one begin sentinel'
assert t.count('<<< second-brain') == 1, 'not exactly one end sentinel'
assert 'line1' in t and 'line2' in t, 'vault body missing'
"
report "append_global_claudemd: creates one block from vault body" $?

# --- Test 9: append preserves pre-existing content --------------------------
printf 'MY EXISTING RULES\n' > "$gmd"
append_global_claudemd "$gmd" "$mdscratch/vault_claude.md"
python3 -c "
t = open('$gmd').read()
assert t.startswith('MY EXISTING RULES'), 'preexisting content not preserved at top'
assert t.count('>>> second-brain') == 1, 'not one block'
"
report "append_global_claudemd: preserves pre-existing content" $?

# --- Test 10: append twice is idempotent, refreshes body --------------------
append_global_claudemd "$gmd" "$mdscratch/vault_claude.md"
printf 'line1\nline2\nline3-new\n' > "$mdscratch/vault_claude.md"
append_global_claudemd "$gmd" "$mdscratch/vault_claude.md"
python3 -c "
t = open('$gmd').read()
assert t.count('>>> second-brain') == 1, f'expected 1 block, found {t.count(\">>> second-brain\")}'
assert 'line3-new' in t, 'block body not refreshed'
assert t.startswith('MY EXISTING RULES'), 'preexisting content lost across re-runs'
"
report "append_global_claudemd: idempotent + refreshes body" $?

# --- Test 11: remove restores pre-existing content --------------------------
remove_global_claudemd_block "$gmd"
python3 -c "
t = open('$gmd').read()
assert 'second-brain' not in t, 'block not removed'
assert t.strip() == 'MY EXISTING RULES', f'preexisting content not restored: {t!r}'
"
report "remove_global_claudemd_block: restores pre-existing content" $?

# --- Test 12: remove when block was the only content deletes the file -------
printf '' > "$gmd"
append_global_claudemd "$gmd" "$mdscratch/vault_claude.md"
remove_global_claudemd_block "$gmd"
[ ! -e "$gmd" ]
report "remove_global_claudemd_block: deletes file when block was sole content" $?

# --- Test 13: remove when file/block absent is a no-op rc=0 -----------------
remove_global_claudemd_block "$mdscratch/does_not_exist.md"
report "remove_global_claudemd_block: absent file is no-op rc=0" $?

printf 'no block here\n' > "$gmd"
remove_global_claudemd_block "$gmd"
grep -qx 'no block here' "$gmd"
report "remove_global_claudemd_block: absent block leaves file untouched" $?

echo
echo "results: $pass passed, $fail failed"
if [ $fail -ne 0 ]; then
  exit 1
fi
exit 0
