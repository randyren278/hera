#!/usr/bin/env bash
# e2e_hooks_foreign_cwd.sh — prove the hooks work when invoked from a
# CWD outside the vault, using $SECOND_BRAIN_VAULT to locate it.
#
# This is the P2 acceptance test for the global-install project.

set -u
VAULT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$VAULT/.venv/bin/python"

fail=0

# Precondition: brain.db and wiki/ populated.
if [ ! -s "$VAULT/brain.db" ] || [ ! -d "$VAULT/wiki/concepts" ]; then
  echo "prereq: running e2e_ingest to populate vault"
  bash "$VAULT/scripts/e2e_ingest.sh" >/tmp/e2e_ingest_for_foreign.log 2>&1 || {
    echo "FAIL: prerequisite ingest did not succeed"; cat /tmp/e2e_ingest_for_foreign.log; exit 1;
  }
fi

echo "== hooks from foreign CWD =="
# Change to /tmp so the hook's *invocation* CWD is not the vault.
# The hook must still find its way home via $SECOND_BRAIN_VAULT.
cd /tmp

# 1) session_start emits something (hot.md contents, at minimum) when
#    SECOND_BRAIN_VAULT points at the vault.
OUT_SS=$(SECOND_BRAIN_VAULT="$VAULT" "$PY" "$VAULT/.claude/hooks/session_start.py" < /dev/null)
if [ -z "$(echo "$OUT_SS" | tr -d '[:space:]')" ]; then
  echo "FAIL: session_start.py produced no output from foreign CWD"
  fail=1
else
  echo "  ok  session_start.py fired from /tmp"
fi

# 2) prompt_inject on a topical prompt still returns a pointer that
#    references a vault page (not something in /tmp).
ON='retrieval-augmented generation'
OUT_PI=$(printf '{"prompt":"tell me about %s"}' "$ON" \
  | SECOND_BRAIN_VAULT="$VAULT" "$PY" "$VAULT/.claude/hooks/prompt_inject.py")
if echo "$OUT_PI" | grep -qi "$ON\|rag\|hybrid retrieval"; then
  echo "  ok  prompt_inject.py returned a vault pointer from /tmp"
else
  echo "FAIL: prompt_inject.py from foreign CWD did not inject a pointer"
  echo "  output was: [$OUT_PI]"
  fail=1
fi

# 3) Make sure the injected pointer references a vault-relative path,
#    not something absolute-in-/tmp.
if echo "$OUT_PI" | grep -q "/tmp/"; then
  echo "FAIL: injected pointer references /tmp — vault path leaked"
  fail=1
fi

if [ $fail -ne 0 ]; then
  echo "== e2e_hooks_foreign_cwd: FAIL =="
  exit 1
fi
echo "== e2e_hooks_foreign_cwd: OK =="
exit 0
