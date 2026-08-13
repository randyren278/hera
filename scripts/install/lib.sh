#!/usr/bin/env bash
# lib.sh — install-time helpers for the Second Brain global installer.
#
# All helpers are idempotent, print actionable errors, and rc=0 on success.
# Sourced by install.sh and by tests/test_install_lib.sh.

# --- backup/restore ---------------------------------------------------------

# backup_file <path>
#   Copies <path> to <path>.brain-backup.<timestamp> if <path> exists.
#   Prints the backup path on stdout on success.
#   No-op with rc=0 if <path> doesn't exist (nothing to back up).
backup_file() {
  local src="$1"
  if [ -z "$src" ]; then
    echo "backup_file: usage: backup_file <path>" >&2
    return 2
  fi
  if [ ! -e "$src" ]; then
    return 0
  fi
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  local dst="${src}.brain-backup.${ts}"
  # In the unlikely event of a same-second collision, append a suffix.
  local n=0
  while [ -e "$dst" ]; do
    n=$((n + 1))
    dst="${src}.brain-backup.${ts}.${n}"
  done
  cp -p "$src" "$dst" || return 1
  echo "$dst"
}

# restore_latest_backup <path>
#   Finds the most recent <path>.brain-backup.* and restores it atomically
#   to <path>. rc=1 if no backup exists.
restore_latest_backup() {
  local target="$1"
  if [ -z "$target" ]; then
    echo "restore_latest_backup: usage: restore_latest_backup <path>" >&2
    return 2
  fi
  local dir base latest
  dir=$(dirname "$target")
  base=$(basename "$target")
  # Newest by mtime. -t sorts by mtime desc; head -1 picks the first.
  latest=$(ls -1t "$dir"/"$base".brain-backup.* 2>/dev/null | head -1)
  if [ -z "$latest" ]; then
    return 1
  fi
  cp -p "$latest" "$target" || return 1
  return 0
}

# --- settings.json JSON-merge ----------------------------------------------

# merge_settings_json <target> <fragment>
#   JSON-merges <fragment> into <target>. Preserves pre-existing keys.
#   Idempotent for the fragments this installer ships (dedupes by
#   command string within each hook-event array).
#   If <target> doesn't exist, it's created from the fragment.
merge_settings_json() {
  local target="$1"
  local fragment="$2"
  if [ -z "$target" ] || [ -z "$fragment" ]; then
    echo "merge_settings_json: usage: merge_settings_json <target> <fragment>" >&2
    return 2
  fi
  if [ ! -f "$fragment" ]; then
    echo "merge_settings_json: fragment not found: $fragment" >&2
    return 1
  fi
  mkdir -p "$(dirname "$target")"

  python3 - "$target" "$fragment" <<'PYEOF' || return 1
import json
import os
import sys

target_path = sys.argv[1]
fragment_path = sys.argv[2]

with open(fragment_path) as f:
    fragment = json.load(f)

if os.path.exists(target_path):
    with open(target_path) as f:
        target = json.load(f)
else:
    target = {}

target.setdefault("hooks", {})
frag_hooks = fragment.get("hooks", {})

# Signature of an entry group: the tuple of command strings inside its
# "hooks" array. Two groups are duplicates iff their signatures match.
def sig(entry):
    return tuple(h.get("command") for h in entry.get("hooks", []))

for event, groups in frag_hooks.items():
    existing = target["hooks"].setdefault(event, [])
    existing_sigs = {sig(e) for e in existing}
    for g in groups:
        if sig(g) not in existing_sigs:
            existing.append(g)
            existing_sigs.add(sig(g))

tmp = target_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(target, f, indent=2)
    f.write("\n")
os.replace(tmp, target_path)
PYEOF
}

# settings_has_our_hooks <target> <fragment>
#   rc=0 iff <target> exists and already contains EVERY hook-group signature
#   from <fragment> (same signature = tuple of command strings, matching
#   merge_settings_json's dedup key). rc=1 otherwise (missing file, missing
#   any group). Used by install to detect a re-install and avoid backing up a
#   settings.json that already has our entries (which would clobber the clean
#   pre-install backup).
settings_has_our_hooks() {
  local target="$1"
  local fragment="$2"
  if [ -z "$target" ] || [ -z "$fragment" ]; then
    echo "settings_has_our_hooks: usage: settings_has_our_hooks <target> <fragment>" >&2
    return 2
  fi
  [ -f "$target" ] || return 1
  [ -f "$fragment" ] || return 1

  python3 - "$target" "$fragment" <<'PYEOF'
import json
import sys

target_path = sys.argv[1]
fragment_path = sys.argv[2]

try:
    with open(target_path) as f:
        target = json.load(f)
    with open(fragment_path) as f:
        fragment = json.load(f)
except (OSError, ValueError):
    sys.exit(1)

def sig(entry):
    return tuple(h.get("command") for h in entry.get("hooks", []))

target_hooks = target.get("hooks", {})
for event, groups in fragment.get("hooks", {}).items():
    existing_sigs = {sig(e) for e in target_hooks.get(event, [])}
    for g in groups:
        if sig(g) not in existing_sigs:
            sys.exit(1)  # a fragment group is missing → not fully installed
sys.exit(0)
PYEOF
}

# strip_our_hooks <target> <fragment>
#   Removes from <target> every hook-group whose signature matches a group in
#   <fragment> (same command-tuple key as merge_settings_json). If <target>
#   becomes an empty hooks map ({"hooks":{}} with no other top-level keys) it
#   is deleted. No-op rc=0 if <target> is absent. Prints one status line.
#   Shared by uninstall's restore path (strip residual hooks from a possibly
#   polluted backup) and its no-backup path (remove our-only settings.json).
strip_our_hooks() {
  local target="$1"
  local fragment="$2"
  if [ -z "$target" ] || [ -z "$fragment" ]; then
    echo "strip_our_hooks: usage: strip_our_hooks <target> <fragment>" >&2
    return 2
  fi
  [ -f "$target" ] || return 0

  python3 - "$target" "$fragment" <<'PYEOF' || return 1
import json
import os
import sys

target = sys.argv[1]
fragment = sys.argv[2]
with open(target) as f:
    d = json.load(f)
with open(fragment) as f:
    frag = json.load(f)

def sig(entry):
    return tuple(h.get("command") for h in entry.get("hooks", []))

frag_sigs = {ev: {sig(g) for g in groups}
             for ev, groups in frag.get("hooks", {}).items()}

# Remove any entry whose signature matches ours.
hooks = d.get("hooks", {})
changed = False
for ev, groups in list(hooks.items()):
    kept = [g for g in groups if sig(g) not in frag_sigs.get(ev, set())]
    if kept != groups:
        changed = True
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]

if not changed:
    print("  no second-brain hook entries to remove")
    sys.exit(0)

# Delete the file only if OUR hooks were its sole content: an empty hooks
# map and no other top-level keys. Otherwise rewrite, preserving everything.
other_keys = [k for k in d.keys() if k != "hooks"]
if not hooks and not other_keys:
    os.remove(target)
    print("  removed settings.json (contained only second-brain hooks)")
else:
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
    os.replace(tmp, target)
    print("  stripped second-brain hook entries from settings.json")
PYEOF
}

# --- global CLAUDE.md marked block ------------------------------------------

# Sentinels delimiting our managed block inside ~/.claude/CLAUDE.md.
# Kept verbose enough to never collide with user-authored content.
BRAIN_MD_BEGIN='# >>> second-brain (managed by install.sh) >>>'
BRAIN_MD_END='# <<< second-brain <<<'

# append_global_claudemd <global_md_path> <vault_md_path>
#   Appends (or replaces in place) a single marked block in <global_md_path>
#   whose body is the verbatim contents of <vault_md_path>. Everything
#   outside the sentinels is preserved byte-for-byte. Backs up an existing
#   target via backup_file first. Idempotent: re-running refreshes the block
#   body and never produces a second block. Atomic write via .tmp + replace.
#   rc=2 on usage error, rc=1 if <vault_md_path> is unreadable.
append_global_claudemd() {
  local target="$1"
  local source="$2"
  if [ -z "$target" ] || [ -z "$source" ]; then
    echo "append_global_claudemd: usage: append_global_claudemd <global_md> <vault_md>" >&2
    return 2
  fi
  if [ ! -r "$source" ]; then
    echo "append_global_claudemd: cannot read vault CLAUDE.md: $source" >&2
    return 1
  fi
  # Back up an existing target (no-op rc=0 if absent).
  backup_file "$target" >/dev/null || return 1

  BRAIN_MD_BEGIN="$BRAIN_MD_BEGIN" BRAIN_MD_END="$BRAIN_MD_END" \
  python3 - "$target" "$source" <<'PYEOF' || return 1
import os
import sys

target_path = sys.argv[1]
source_path = sys.argv[2]
begin = os.environ["BRAIN_MD_BEGIN"]
end = os.environ["BRAIN_MD_END"]

with open(source_path) as f:
    vault_body = f.read()

note = ("# This block is managed by the Second Brain install.sh. It mirrors\n"
        "# the global CLAUDE.md so citation-learning and safety invariants\n"
        "# stay active in every directory. Remove it with: install.sh --uninstall\n")
block = begin + "\n" + note + "\n" + vault_body.rstrip("\n") + "\n" + end + "\n"

if os.path.exists(target_path):
    with open(target_path) as f:
        existing = f.read()
else:
    existing = ""

b = existing.find(begin)
if b != -1:
    e = existing.find(end, b)
    if e != -1:
        e_end = e + len(end)
        # Swallow a trailing newline after the end sentinel so replacement
        # doesn't accumulate blank lines across re-runs.
        if e_end < len(existing) and existing[e_end] == "\n":
            e_end += 1
        new = existing[:b] + block + existing[e_end:]
    else:
        # Begin sentinel without a matching end — treat as corrupt; append fresh.
        new = existing + ("\n" if existing and not existing.endswith("\n") else "") + block
else:
    if existing and not existing.endswith("\n"):
        existing += "\n"
    sep = "\n" if existing else ""
    new = existing + sep + block

tmp = target_path + ".tmp"
with open(tmp, "w") as f:
    f.write(new)
os.replace(tmp, target_path)
PYEOF
}

# remove_global_claudemd_block <global_md_path>
#   Removes exactly the sentinel-to-sentinel span (inclusive) from
#   <global_md_path>, preserving all surrounding content. No-op rc=0 if the
#   file or block is absent. If the file is whitespace-only after removal,
#   it is deleted. Atomic write via .tmp + replace.
remove_global_claudemd_block() {
  local target="$1"
  if [ -z "$target" ]; then
    echo "remove_global_claudemd_block: usage: remove_global_claudemd_block <global_md>" >&2
    return 2
  fi
  if [ ! -f "$target" ]; then
    return 0
  fi

  BRAIN_MD_BEGIN="$BRAIN_MD_BEGIN" BRAIN_MD_END="$BRAIN_MD_END" \
  python3 - "$target" <<'PYEOF' || return 1
import os
import sys

target_path = sys.argv[1]
begin = os.environ["BRAIN_MD_BEGIN"]
end = os.environ["BRAIN_MD_END"]

with open(target_path) as f:
    existing = f.read()

b = existing.find(begin)
if b == -1:
    sys.exit(0)  # no block — nothing to do
e = existing.find(end, b)
if e == -1:
    sys.exit(0)  # corrupt/no end — leave alone rather than guess
e_end = e + len(end)
if e_end < len(existing) and existing[e_end] == "\n":
    e_end += 1
# Also drop one blank separator line immediately before the block, if that
# is what append added between prior content and our block.
start = b
if start >= 1 and existing[start - 1] == "\n" and (start < 2 or existing[start - 2] == "\n"):
    start -= 1

new = existing[:start] + existing[e_end:]

if new.strip() == "":
    os.remove(target_path)
    sys.exit(0)

tmp = target_path + ".tmp"
with open(tmp, "w") as f:
    f.write(new)
os.replace(tmp, target_path)
PYEOF
}

# --- environment preflight --------------------------------------------------

# require_env
#   Prints actionable errors if required tools are missing. rc=1 on failure.
require_env() {
  local fail=0
  if [ -z "${BASH_VERSION:-}" ]; then
    echo "require_env: bash is required" >&2
    fail=1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "require_env: python3 not found on PATH" >&2
    fail=1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "require_env: git not found on PATH" >&2
    fail=1
  fi
  # `readlink -f` behaves differently on BSD/macOS vs GNU. We don't rely
  # on it — we use python for absolute-path resolution — but flag if a
  # future maintainer needs it.
  return $fail
}
