#!/usr/bin/env bash
# install.sh — thin POSIX shim. The real installer is install.py (cross-platform).
# Kept so existing docs/muscle-memory ("bash install.sh") still work on POSIX.
exec python3 "$(dirname "$0")/install.py" "$@"
