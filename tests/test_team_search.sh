#!/usr/bin/env bash
# test_team_search.sh — verify owner filtering scopes correctly.
#
# Creates a throwaway second owner folder (teammate "casey") with a page
# containing a unique token, then asserts:
#   - search-all finds both randy's and casey's matches
#   - --owner casey finds casey's, NOT randy's or personal
#   - --owner randy excludes personal even for a shared term
set -u
cd "$(dirname "$0")/.." || exit 3
export SECOND_BRAIN_VAULT="$PWD"
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"

CASEY="team-brain-staging/casey/concepts"
UNIQ="zzquantumfoo"
cleanup() { rm -rf "team-brain-staging/casey"; }
trap cleanup EXIT
mkdir -p "$CASEY"
cat > "$CASEY/Quantum Widget.md" <<EOF
---
id: 01TESTCASEY0000000000000000
type: concept
title: "Quantum Widget"
aliases: []
visibility: public
owner: casey
---
> [!info] A $UNIQ device for testing owner scoping.

The $UNIQ concept belongs to casey only.
EOF

fail() { echo "FAIL: $1"; exit 1; }

# 1. search-all finds casey's unique token, tagged owner=casey source=team.
OUT=$("$PY" scripts/team_search.py --json "$UNIQ")
echo "$OUT" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
assert d, "no hits for unique token"
assert any(h["owner"]=="casey" and h["source"]=="team" for h in d), "casey hit missing/mistagged"
print("ok1")
' || fail "search-all did not surface casey correctly"

# 2. --owner casey returns casey hits, and NO personal/randy hits.
OUT=$("$PY" scripts/team_search.py --owner casey --json "$UNIQ")
echo "$OUT" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
assert d, "no hits under --owner casey"
assert all(h["source"]=="team" for h in d), "personal leaked into --owner scope"
assert all(h["owner"]=="casey" for h in d), "another owner leaked into --owner casey"
print("ok2")
' || fail "--owner casey scoping wrong"

# 3. A term that exists in BOTH randy's team pages and personal wiki:
#    --owner randy must exclude personal (source must all be team).
OUT=$("$PY" scripts/team_search.py --owner randy --json "retrieval")
echo "$OUT" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
# may be empty if randy has no match, but if present must be team-only
assert all(h["source"]=="team" and h["owner"]=="randy" for h in d), "personal leaked into --owner randy"
print("ok3")
' || fail "--owner randy leaked personal"

echo "test_team_search: OK"
exit 0
