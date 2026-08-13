#!/usr/bin/env bash
# preflight.sh — thin POSIX shim. The real checks live in
# scripts/install/preflight.py (OS-neutral). This shim bootstraps the venv if
# absent (via venv.py), then runs the Python preflight under the venv python so
# the library-import checks reflect the venv, not the system python.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"

if [ ! -x "$PY" ]; then
  # Bootstrap the venv with whatever python3 is available; venv.py picks a
  # bootstrap interpreter and installs deps.
  python3 "$REPO/scripts/install/venv.py" "$REPO" >/dev/null || {
    echo "preflight: venv bootstrap failed" >&2; exit 1;
  }
fi

exec "$PY" "$REPO/scripts/install/preflight.py" "$@"
