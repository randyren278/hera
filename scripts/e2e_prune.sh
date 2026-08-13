#!/usr/bin/env bash
# e2e_prune.sh — CP-8 check for /brain-prune.
#
# Approach: seed a synthetic scoreboard (backdated ages + varying citation
# totals across 10 concept pages, 1 old source, 1 young concept). Run prune.
# Assert middle-band archived; top/bottom preserved; sources untouched; young
# page not eligible; restore roundtrips.
#
# Modes:
#   default            : full seed + apply + verify.
#   --grep-sources     : verify no source page was archived (independent check).
#   --young-page       : verify a page younger than 30 days is not eligible.
#   --restore          : archive one page then restore it, verify roundtrip.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"

MODE="${1:-default}"

# Seed layout (all under wiki/prune_synth/):
#   10 concept pages, IDs 01PRUNECON00..09, ages ~40 days.
#   Citation totals: 00→0, 01→2, 02→5, 03→7, 04→10, 05→12, 06→15, 07→20, 08→30, 09→50 pts
#   Prune band 40-70% of 10 items = indexes [4,5,6] → 3 pages archived.
#   Top-3 (indices 7-9) and bottom-4 (0-3) preserved.
#   1 source page, always preserved regardless of citations.
#   1 young concept page (age = 5 days) with 0 citations — must NOT be eligible.
seed_synth() {
  $PY - <<'PY'
import sys, time, pathlib
sys.path.insert(0, "scripts")
import brain_db
c = brain_db.connect()

pathlib.Path("wiki/prune_synth").mkdir(parents=True, exist_ok=True)

# Clean any previous synth state (order matters: children before pages).
c.execute("DELETE FROM citations WHERE page_id LIKE '01PRUNE%'")
c.execute("DELETE FROM pages_vec WHERE page_id LIKE '01PRUNE%'")
c.execute("DELETE FROM pages_fts_map WHERE page_id LIKE '01PRUNE%'")
c.execute("DELETE FROM pages WHERE id LIKE '01PRUNE%'")
c.commit()

old_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 40 * 86400))
young_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 5 * 86400))

tiers = [0, 2, 5, 7, 10, 12, 15, 20, 30, 50]
for i, pts in enumerate(tiers):
    pid = f"01PRUNECON{i:02d}0000000000000000"
    title = f"Prune Synth Concept {i}"
    path = f"wiki/prune_synth/concept-{i}.md"
    pathlib.Path(path).write_text(f"# {title}\n\nSynthetic concept for prune testing (tier={pts}).")
    c.execute("INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at) "
              "VALUES (?, ?, '[]', 'concept', ?, ?, ?)",
              (pid, title, path, old_iso, old_iso))
    if pts:
        c.execute("INSERT INTO citations(page_id, session_id, tier, points, cited_at) "
                  "VALUES (?, 'synth-sess', 'final', ?, ?)", (pid, pts, old_iso))

src_id = "01PRUNESRC000000000000000000"
src_path = "wiki/prune_synth/source-untouchable.md"
pathlib.Path(src_path).write_text("# Source — must never prune\n")
c.execute("INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at) "
          "VALUES (?, 'Prune Synth Source', '[]', 'source', ?, ?, ?)",
          (src_id, src_path, old_iso, old_iso))

yng_id = "01PRUNEYNG000000000000000000"
yng_path = "wiki/prune_synth/young-concept.md"
pathlib.Path(yng_path).write_text("# Young Concept\n")
c.execute("INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at) "
          "VALUES (?, 'Young Concept', '[]', 'concept', ?, ?, ?)",
          (yng_id, yng_path, young_iso, young_iso))
c.commit()
print(f"seeded 10 concepts + 1 source + 1 young page")
PY
}

get_count() {
  local sql="$1"
  $PY - <<PY
import sys; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
print(c.execute("$sql").fetchone()[0])
PY
}

case "$MODE" in
  --grep-sources)
    seed_synth
    $PY scripts/prune.py apply --yes >/dev/null
    N=$(get_count "SELECT count(*) FROM pages WHERE archived_at IS NOT NULL AND type='source' AND id LIKE '01PRUNE%'")
    [ "$N" = "0" ] || { echo "FAIL: $N source page(s) got archived"; exit 1; }
    echo "no source page archived"
    exit 0
    ;;
  --young-page)
    seed_synth
    OUT=$($PY scripts/prune.py candidates --json)
    echo "$OUT" | $PY -c "
import json,sys
band = json.load(sys.stdin)
young_in = any(c['page_id'] == '01PRUNEYNG000000000000000000' for c in band)
sys.exit(1 if young_in else 0)
" || { echo "FAIL: young page appeared in prune band"; exit 1; }
    echo "young page preserved"
    exit 0
    ;;
  --restore)
    seed_synth
    $PY scripts/prune.py apply --yes >/dev/null
    PID=$($PY - <<'PY'
import sys; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
r = c.execute("SELECT id FROM pages WHERE archived_at IS NOT NULL AND id LIKE '01PRUNECON%' LIMIT 1").fetchone()
print(r[0] if r else "")
PY
)
    [ -n "$PID" ] || { echo "FAIL: nothing was archived"; exit 1; }
    $PY scripts/prune.py restore "$PID" >/dev/null
    STATE=$($PY - <<PY
import sys; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
r = c.execute("SELECT archived_at, path FROM pages WHERE id = ?", ("$PID",)).fetchone()
print((r[0] or "NULL"), r[1])
PY
)
    ARCHIVED_AT=$(echo "$STATE" | awk '{print $1}')
    PATH_=$(echo "$STATE" | cut -d' ' -f2-)
    [ "$ARCHIVED_AT" = "NULL" ] || { echo "FAIL: archived_at not cleared ($ARCHIVED_AT)"; exit 1; }
    test -f "$PATH_" || { echo "FAIL: restored file missing at $PATH_"; exit 1; }
    echo "restore roundtrip OK ($PID)"
    exit 0
    ;;
esac

echo "== e2e_prune: seed =="
seed_synth

echo "== e2e_prune: candidates =="
$PY scripts/prune.py candidates
BAND=$($PY scripts/prune.py candidates --json)
BAND_COUNT=$(echo "$BAND" | $PY -c "import sys,json; print(len(json.load(sys.stdin)))")
echo "  band size: $BAND_COUNT"
[ "$BAND_COUNT" = "3" ] || { echo "FAIL: expected 3 in prune band, got $BAND_COUNT"; exit 1; }

BAND_TITLES=$(echo "$BAND" | $PY -c "import sys,json; [print(c['title']) for c in json.load(sys.stdin)]")
echo "  band titles: $BAND_TITLES"
echo "$BAND_TITLES" | grep -q 'Concept 4' || { echo "FAIL: band missing Concept 4"; exit 1; }
echo "$BAND_TITLES" | grep -q 'Concept 5' || { echo "FAIL: band missing Concept 5"; exit 1; }
echo "$BAND_TITLES" | grep -q 'Concept 6' || { echo "FAIL: band missing Concept 6"; exit 1; }

echo "== e2e_prune: apply =="
$PY scripts/prune.py apply --yes >/tmp/prune_apply.json
$PY -c "import json; d=json.load(open('/tmp/prune_apply.json')); print('applied:', d['count']); assert d['count'] == 3"

for i in 4 5 6; do
  test ! -f "wiki/prune_synth/concept-$i.md" || { echo "FAIL: concept-$i.md still in original path"; exit 1; }
done
ARCH_COUNT=$(find wiki/.archive -name "01PRUNECON*.md" | wc -l | tr -d ' ')
[ "$ARCH_COUNT" = "3" ] || { echo "FAIL: expected 3 archived files, got $ARCH_COUNT"; exit 1; }

$PY - <<'PY'
import sys; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
n = c.execute("SELECT count(*) FROM pages WHERE archived_at IS NOT NULL AND id LIKE '01PRUNECON%'").fetchone()[0]
assert n == 3, f"expected 3 archived, got {n}"
gone = c.execute("SELECT count(*) FROM pages_vec WHERE page_id IN "
                 "('01PRUNECON040000000000000000','01PRUNECON050000000000000000','01PRUNECON060000000000000000')").fetchone()[0]
assert gone == 0, f"vec rows for archived pages still present: {gone}"
print("db state OK")
PY

grep -q '^## \[.*\] prune' wiki/log.md || { echo "FAIL: log.md missing prune entry"; exit 1; }

NSRC=$(get_count "SELECT count(*) FROM pages WHERE archived_at IS NOT NULL AND type='source' AND id LIKE '01PRUNE%'")
[ "$NSRC" = "0" ] || { echo "FAIL: $NSRC source page(s) archived"; exit 1; }

YNG=$(get_count "SELECT count(*) FROM pages WHERE archived_at IS NOT NULL AND id='01PRUNEYNG000000000000000000'")
[ "$YNG" = "0" ] || { echo "FAIL: young page was archived"; exit 1; }

echo "== e2e_prune: OK =="
