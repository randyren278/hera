#!/usr/bin/env bash
# check_inject_paths_resolve.sh — reproduce + verify the injected-pointer fix.
#
# The bug: prompt_inject.py emits bare vault-relative paths (wiki/sources/X.md).
# An agent running from a CWD other than the vault can't resolve them, misreads
# correct pointers as a "stale index", and wastes a search. The fix makes the
# emitted path resolvable from ANY cwd.
#
# This check is hermetic: it builds a scratch vault, stubs search.py + brain_db.py
# (so no Ollama / real brain.db needed), runs the hook FROM /tmp, extracts the
# path token from the pointer line, and asserts a file exists at that path
# WITHOUT first cd-ing into the vault. Red on current code, green after the fix.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/.claude/hooks/prompt_inject.py"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

VAULT="$SCRATCH/vault"
mkdir -p "$VAULT/wiki/sources" "$VAULT/scripts"

# A real page at a vault-relative path.
cat > "$VAULT/wiki/sources/probe.md" <<'MD'
---
title: "Probe Page"
---
> [!source] A probe page used to verify injected pointers resolve from any cwd.
MD

# Stub search.py: return one Hit with the vault-relative path, matching the
# real dataclass shape (page_id, title, path, score, ...).
cat > "$VAULT/scripts/search.py" <<'PY'
from dataclasses import dataclass
@dataclass
class Hit:
    page_id: str
    title: str
    path: str
    score: float = 1.0
def hybrid_search(conn, query, top_n=3, floor=0.0):
    return [Hit(page_id="P1", title="Probe Page", path="wiki/sources/probe.md")]
PY

# Stub brain_db.py: connect() returns an object whose .execute() serves the two
# config rows the hook reads (inject_top_n, inject_relevance_floor) and an empty
# conflicts result.
cat > "$VAULT/scripts/brain_db.py" <<'PY'
class _Cur:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows
class _Conn:
    def execute(self, sql, params=()):
        s = sql.lower()
        if "inject_top_n" in s: return _Cur([("3",)])
        if "inject_relevance_floor" in s: return _Cur([("0.0",)])
        return _Cur([])  # conflicts query -> none
def connect(*a, **k): return _Conn()
PY

export SECOND_BRAIN_VAULT="$VAULT"

# Run the hook from a NON-vault cwd, feeding a prompt as JSON on stdin.
OUT=$(cd /tmp && printf '{"prompt":"what do you know about probe pages and product managers"}' \
      | python3 "$HOOK" 2>/dev/null)

echo "--- hook output ---"
echo "$OUT"
echo "-------------------"

# Extract the path token from the pointer line: it's inside the parentheses
# "( ... )" on the "- [[Title]] ( PATH ) — ..." line.
LINE=$(echo "$OUT" | grep -F '[[Probe Page]]' | head -1)
if [ -z "$LINE" ]; then
  echo "FAIL: no pointer line for the probe page was emitted"; exit 1
fi
PATH_TOKEN=$(echo "$LINE" | sed -n 's/.*(\(.*\)).*/\1/p' | sed 's/^ *//; s/ *$//')
if [ -z "$PATH_TOKEN" ]; then
  echo "FAIL: could not extract a path token from: $LINE"; exit 1
fi
echo "extracted path token: $PATH_TOKEN"

# The core assertion: resolve the token from a NON-vault cwd. A bare relative
# path (wiki/sources/probe.md) will NOT exist relative to /tmp -> red.
if ( cd /tmp && test -f "$PATH_TOKEN" ); then
  echo "OK: injected path resolves to a real file from a non-vault cwd"
  exit 0
fi
echo "FAIL: '$PATH_TOKEN' does not resolve from a non-vault cwd (bare relative path bug)"
exit 1
