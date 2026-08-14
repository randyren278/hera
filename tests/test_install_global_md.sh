#!/usr/bin/env bash
# test_install_global_md.sh — CP-2/CP-3 tests for the optional global
# CLAUDE.md install step.
#
# Scope: the append/remove behavior and the install-step decision logic
# (flag / TTY / dry-run). It deliberately does NOT boot the full install
# (preflight pings Ollama and bootstraps .venv — out of scope here; the
# helpers and dry-run paths are what this feature adds). The append/remove
# helpers themselves are also covered in test_install_lib.sh.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# shellcheck disable=SC1091
source "$REPO/scripts/install/lib.sh"

pass=0; fail=0
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

report() {
  local name="$1" rc="$2"
  if [ "$rc" -eq 0 ]; then
    echo "  ok    $name"; pass=$((pass + 1))
  else
    echo "  FAIL  $name"; fail=$((fail + 1))
  fi
}

# --- Case A: --dry-run --with-global-claudemd writes nothing ----------------
# Dry-run never executes preflight (it only echoes), so this is hermetic.
A="$scratch/A"; mkdir -p "$A/.claude"
HOME="$A" bash install.sh --dry-run --with-global-claudemd >/dev/null 2>&1
[ ! -e "$A/.claude/CLAUDE.md" ]
report "dry-run --with-global-claudemd: creates no CLAUDE.md" $?

# --- Case B: --dry-run mentions the optional step ---------------------------
out=$(HOME="$A" bash install.sh --dry-run --with-global-claudemd 2>&1)
echo "$out" | grep -q "step 8/8: global CLAUDE.md"
report "dry-run: prints the global CLAUDE.md step banner" $?

# --- Case C: append via the helper (flag path the step uses) preserves prior
#            content and embeds the tracked CLAUDE.md, which is the consumer
#            version — banner-free (the repo-role banner lives in MAINTAINERS.md) -
C="$scratch/C"; mkdir -p "$C/.claude"
printf 'USER GLOBAL RULES\n' > "$C/.claude/CLAUDE.md"
CLAUDE_HOME="$C/.claude" append_global_claudemd "$C/.claude/CLAUDE.md" "$REPO/CLAUDE.md"
python3 -c "
t = open('$C/.claude/CLAUDE.md').read()
v = open('$REPO/CLAUDE.md').read()
assert t.startswith('USER GLOBAL RULES'), 'preexisting content lost'
assert t.count('>>> Hera') == 1, 'not exactly one block'
assert v.strip() in t, 'CLAUDE.md not embedded verbatim'
assert 'SOURCE / TEMPLATE' not in t, 'repo-role banner leaked into global mirror'
assert 'Never push to' in t, 'invariants missing from global mirror'
"
report "append: preserves user content + embeds CLAUDE.md (banner-free)" $?

# a backup of the prior CLAUDE.md must exist
ls "$C/.claude/CLAUDE.md".hera-backup.* >/dev/null 2>&1
report "append: backed up the pre-existing CLAUDE.md" $?

# --- Case D: uninstall roundtrip restores user content byte-for-byte --------
# The uninstall step calls remove_global_claudemd_block; exercise it directly
# to keep the test hermetic (no preflight).
remove_global_claudemd_block "$C/.claude/CLAUDE.md"
diff <(printf 'USER GLOBAL RULES\n') "$C/.claude/CLAUDE.md" >/dev/null
report "remove: restores user content byte-for-byte" $?

# --- Case E: block-only file is deleted on removal --------------------------
E="$scratch/E"; mkdir -p "$E/.claude"
: > "$E/.claude/CLAUDE.md"
append_global_claudemd "$E/.claude/CLAUDE.md" "$REPO/CLAUDE.md"
remove_global_claudemd_block "$E/.claude/CLAUDE.md"
[ ! -e "$E/.claude/CLAUDE.md" ]
report "remove: deletes file when the block was the only content" $?

echo
echo "results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
exit 0
