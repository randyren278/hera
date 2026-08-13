#!/usr/bin/env bash
# test_team_remove.sh — prove team_remove is owner-scoped and never pushes.
#
# Builds a throwaway vault in a temp dir with its own team-brain-staging git
# repo (NO remote — so any accidental push would fail loudly, and we never
# touch the real clone). Two owners: randy (the caller) and casey (foreign).
#
# Asserts:
#   ok1 — list returns randy's page(s), NOT casey's (owner-scoped)
#   ok2 — stage-remove of a casey/ path is REFUSED (non-zero) and stages nothing
#   ok3 — stage-remove of a randy/ path stages a deletion (git status shows D),
#         and nothing is pushed (fixture has no remote; we assert staged-not-pushed)
set -u

SRC_REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$SRC_REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"

# Throwaway vault.
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

export SECOND_BRAIN_VAULT="$TMP"
export BRAIN_OWNER="randy"

STAGING="$TMP/team-brain-staging"
mkdir -p "$STAGING/randy/concepts" "$STAGING/casey/concepts"

page() {  # page <path> <owner> <title>
  cat > "$1" <<EOF
---
id: 01TEST0000000000000000000000
type: concept
title: "$3"
aliases: []
visibility: public
owner: $2
---
> [!info] fixture page for $2.

Body for $3.
EOF
}
page "$STAGING/randy/concepts/Randy Note.md" randy "Randy Note"
page "$STAGING/randy/concepts/Randy Two.md"  randy "Randy Two"
page "$STAGING/casey/concepts/Casey Note.md" casey "Casey Note"

# Make it a real git repo with history (so git-date works), but NO remote.
git -C "$STAGING" init -q
git -C "$STAGING" config user.email t@t.t
git -C "$STAGING" config user.name tester
git -C "$STAGING" add -A
git -C "$STAGING" commit -qm "fixture: initial pages"

fail() { echo "FAIL: $1"; exit 1; }

# ok1 — list is owner-scoped to randy.
OUT=$("$PY" "$SRC_REPO/scripts/team_remove.py" list --json)
echo "$OUT" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
assert d, "list empty"
assert all("/randy/" in ("/"+h["path"]) for h in d), d
assert all("casey" not in h["path"] for h in d), "casey leaked into randy list"
assert {h["title"] for h in d} == {"Randy Note","Randy Two"}, d
print("ok1")
' || fail "list not owner-scoped"

# ok2 — removing a casey/ path is refused, and stages nothing.
CASEY_PATH="team-brain-staging/casey/concepts/Casey Note.md"
if "$PY" "$SRC_REPO/scripts/team_remove.py" stage-remove "$CASEY_PATH" >/dev/null 2>&1; then
  fail "foreign-owner stage-remove should have exited non-zero (refused)"
fi
# nothing staged: no changes in the index
if [ -n "$(git -C "$STAGING" diff --cached --name-only)" ]; then
  fail "foreign-owner removal staged something — must be fail-closed"
fi
# casey file still present
[ -f "$STAGING/casey/concepts/Casey Note.md" ] || fail "casey file was removed — scope breach"
echo "ok2"

# ok3 — removing a randy/ path stages a deletion but does NOT push.
RANDY_PATH="team-brain-staging/randy/concepts/Randy Note.md"
"$PY" "$SRC_REPO/scripts/team_remove.py" stage-remove "$RANDY_PATH" >/dev/null 2>&1 \
  || fail "in-scope stage-remove should succeed"
# staged as a deletion (porcelain shows 'D' in the index column)
git -C "$STAGING" status --porcelain | grep -qE '^D' \
  || fail "randy removal was not staged as a deletion"
# staged-not-pushed: fixture has no remote, so there is nothing to have pushed to.
[ -z "$(git -C "$STAGING" remote)" ] || fail "fixture unexpectedly has a remote"
# the change is only in the index, not committed (HEAD still has the file)
git -C "$STAGING" cat-file -e "HEAD:randy/concepts/Randy Note.md" 2>/dev/null \
  || fail "removal was committed, not merely staged — must wait for human push"
echo "ok3"

echo "test_team_remove: OK"
exit 0
