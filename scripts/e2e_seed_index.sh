#!/usr/bin/env bash
# e2e_seed_index.sh — CP-3 check for the seed-pack indexer.
#
# Runs scripts/seed_index.py against tests/fixtures/seed-pack into a FRESH temp brain.db
# (via the BRAIN_DB override, so the live index is never touched), then asserts:
#   - pages / pages_fts / pages_vec each == 4 after the first run
#   - a SECOND run adds ZERO rows (idempotent on the baked ULID)
#   - the seed rows are pinned=1
#
# The indexer copies pages into wiki/<type>s/. This template wiki ships empty,
# so we snapshot which concept/entity files existed before and delete only what
# the test added on exit — the "ships empty" invariant survives the test.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
PACK="tests/fixtures/seed-pack"
EXPECT=4

TMPDIR_="$(mktemp -d)"
export BRAIN_DB="$TMPDIR_/brain.db"

# Snapshot pre-existing wiki concept/entity files so we can remove only ours.
BEFORE="$(mktemp)"
find wiki/concepts wiki/entities -name '*.md' 2>/dev/null | sort > "$BEFORE"

cleanup() {
  # Remove wiki files this test created (added since BEFORE snapshot).
  find wiki/concepts wiki/entities -name '*.md' 2>/dev/null | sort > "$TMPDIR_/after" || true
  comm -13 "$BEFORE" "$TMPDIR_/after" 2>/dev/null | while IFS= read -r f; do
    [ -n "$f" ] && rm -f "$f"
  done
  # Remove any stray misspelled dir a regression would (re)create.
  rm -rf wiki/entitys 2>/dev/null || true
  # Remove any stale lockfiles the writes left behind.
  find wiki/concepts wiki/entities -name '.*.md.lock' -delete 2>/dev/null || true
  rm -rf "$TMPDIR_" "$BEFORE"
}
trap cleanup EXIT

# Start from a clean placement state: a stray misspelled dir from a prior
# buggy run would otherwise mask the regression this test now guards against.
rm -rf wiki/entitys 2>/dev/null || true

echo "== e2e_seed_index: init fresh temp db ($BRAIN_DB) =="
$PY scripts/brain_db.py --init >/dev/null

count() {
  $PY - <<PY
import os, sys, pathlib
sys.path.insert(0, "scripts")
import brain_db
c = brain_db.connect(pathlib.Path(os.environ["BRAIN_DB"]))
print(c.execute("SELECT count(*) FROM $1").fetchone()[0])
PY
}

echo "== e2e_seed_index: first run =="
$PY scripts/seed_index.py "$PACK" --json

P1=$(count pages); F1=$(count pages_fts); V1=$(count pages_vec)
echo "  after run 1: pages=$P1 fts=$F1 vec=$V1"
[ "$P1" = "$EXPECT" ] || { echo "FAIL: pages=$P1 != $EXPECT"; exit 1; }
[ "$F1" = "$EXPECT" ] || { echo "FAIL: pages_fts=$F1 != $EXPECT"; exit 1; }
[ "$V1" = "$EXPECT" ] || { echo "FAIL: pages_vec=$V1 != $EXPECT"; exit 1; }

echo "== e2e_seed_index: seed rows pinned =="
PINNED=$(count "pages WHERE pinned=1")
echo "  pinned rows: $PINNED"
[ "$PINNED" = "$EXPECT" ] || { echo "FAIL: pinned=$PINNED != $EXPECT (seed rows not all pinned)"; exit 1; }

echo "== e2e_seed_index: file placement (correct dirs, no misspelled entitys) =="
# The fixture pack ships 2 concepts + 2 entities. Assert they landed in the CORRECT
# wiki subdirs on disk — this is the gap that let the entity->entitys bug pass.
EXP_CONCEPTS=$(ls "$PACK"/concepts/*.md 2>/dev/null | wc -l | tr -d ' ')
EXP_ENTITIES=$(ls "$PACK"/entities/*.md 2>/dev/null | wc -l | tr -d ' ')
# Count only the files THIS run added (delta vs BEFORE snapshot), per subdir.
find wiki/concepts wiki/entities -name '*.md' 2>/dev/null | sort > "$TMPDIR_/placed"
GOT_CONCEPTS=$(comm -13 "$BEFORE" "$TMPDIR_/placed" | grep -c '^wiki/concepts/' || true)
GOT_ENTITIES=$(comm -13 "$BEFORE" "$TMPDIR_/placed" | grep -c '^wiki/entities/' || true)
echo "  placed: concepts=$GOT_CONCEPTS (expect $EXP_CONCEPTS)  entities=$GOT_ENTITIES (expect $EXP_ENTITIES)"
[ "$GOT_CONCEPTS" = "$EXP_CONCEPTS" ] || { echo "FAIL: concepts placed in wiki/concepts/ = $GOT_CONCEPTS != $EXP_CONCEPTS"; exit 1; }
[ "$GOT_ENTITIES" = "$EXP_ENTITIES" ] || { echo "FAIL: entities placed in wiki/entities/ = $GOT_ENTITIES != $EXP_ENTITIES"; exit 1; }
# The misspelled directory must NOT exist.
! test -d wiki/entitys || { echo "FAIL: misspelled dir wiki/entitys/ exists — entity pluralization regressed"; exit 1; }
echo "  no wiki/entitys/ dir — placement correct"
# The path recorded in pages.path must also use the correct dirs.
BADPATH=$(count "pages WHERE path LIKE 'wiki/entitys/%' OR path NOT LIKE 'wiki/%'")
[ "$BADPATH" = "0" ] || { echo "FAIL: $BADPATH pages.path rows point outside wiki/<correct-dir>/"; exit 1; }

echo "== e2e_seed_index: second run (must add ZERO rows) =="
$PY scripts/seed_index.py "$PACK" --json

P2=$(count pages); F2=$(count pages_fts); V2=$(count pages_vec)
echo "  after run 2: pages=$P2 fts=$F2 vec=$V2"
[ "$P2" = "$EXPECT" ] || { echo "FAIL: pages grew to $P2 on second run"; exit 1; }
[ "$F2" = "$EXPECT" ] || { echo "FAIL: pages_fts grew to $F2 on second run"; exit 1; }
[ "$V2" = "$EXPECT" ] || { echo "FAIL: pages_vec grew to $V2 on second run"; exit 1; }

echo "== e2e_seed_index: OK (4/4/4, idempotent, pinned) =="
