#!/usr/bin/env bash
# check_skill_invocations.sh — assert no SKILL.md carries a POSIX-only
# invocation construct. The canonical checker is tests/test_skill_invocations.py
# (run under pytest, OS-neutral); this shim keeps the bash consistency-check
# surface working and delegates to it when a venv python is available.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
if [ -x "$PY" ]; then
  exec "$PY" -m pytest "$REPO/tests/test_skill_invocations.py" -q
fi

# Fallback: grep the banned constructs directly (no venv present).
cd "$REPO"
fail=0
for s in .claude/skills/brain-*/SKILL.md; do
  if grep -nE '\.venv/bin/python|\.venv\\Scripts|^\s*\. "\$HOME|\$\{[A-Z_]+:\?|bash +install\.sh|bash +.*preflight\.sh' "$s" >/tmp/skill_hits.$$; then
    echo "FAIL: $s has POSIX-only constructs:"
    sed 's/^/    /' /tmp/skill_hits.$$
    fail=1
  fi
  rm -f /tmp/skill_hits.$$
done
if [ $fail -ne 0 ]; then
  echo "check_skill_invocations: FAIL — use: python \"<VAULT>/scripts/brain_cli.py\" <engine>"
  exit 1
fi
echo "check_skill_invocations: OK"
exit 0
