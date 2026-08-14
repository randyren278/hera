#!/usr/bin/env bash
# e2e_seed_guards.sh — CP-4 checks for the Phase-4 seed guards.
#
# Two subcommands, each on a FRESH temp hera.db (via HERA_DB override, so the
# live index is never touched). Both index tests/fixtures/seed-pack first so real pinned
# seed pages exist, then assert the guard:
#
#   prune    — pinned seed pages are never eligible pruning candidates, even
#              when artificially aged well past prune_min_age_days.
#   conflict — a NON-session (file) user note that contradicts a seed page's
#              claim at the SAME path auto-resolves USER-WINS: no open conflict
#              row, page body carries the user's new content (## Update /
#              ## Superseded), and the page is not left frozen/stale.
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

SUB="${1:-}"
if [ "$SUB" != "prune" ] && [ "$SUB" != "conflict" ]; then
  echo "usage: $0 prune|conflict" >&2
  exit 2
fi

TMPDIR_="$(mktemp -d)"
export HERA_DB="$TMPDIR_/hera.db"

# Snapshot pre-existing wiki files so we can remove only ours. The conflict
# subcommand also writes a source page (wiki/sources/) and a raw copy
# (wiki/.raw/articles/), so snapshot those too — the template wiki ships empty.
BEFORE="$(mktemp)"
find wiki/concepts wiki/entities wiki/sources wiki/.raw/articles \( -name '*.md' -o -name '*.txt' \) 2>/dev/null | sort > "$BEFORE"

cleanup() {
  find wiki/concepts wiki/entities wiki/sources wiki/.raw/articles \( -name '*.md' -o -name '*.txt' \) 2>/dev/null | sort > "$TMPDIR_/after" || true
  comm -13 "$BEFORE" "$TMPDIR_/after" 2>/dev/null | while IFS= read -r f; do
    [ -n "$f" ] && rm -f "$f"
  done
  rm -rf wiki/entitys 2>/dev/null || true
  find wiki/concepts wiki/entities wiki/sources -name '.*.md.lock' -delete 2>/dev/null || true
  # Restore the tracked meta pages that seed_index/ingest mutate as a side
  # effect, byte-for-byte, so the working tree is left exactly as found.
  for m in hot index log; do
    [ -f "$TMPDIR_/meta.$m.md" ] && cp -p "$TMPDIR_/meta.$m.md" "wiki/$m.md"
  done
  rm -rf "$TMPDIR_" "$BEFORE"
}
trap cleanup EXIT

# Snapshot tracked meta pages before any indexing/ingest mutates them.
for m in hot index log; do
  [ -f "wiki/$m.md" ] && cp -p "wiki/$m.md" "$TMPDIR_/meta.$m.md"
done

echo "== e2e_seed_guards[$SUB]: init fresh temp db ($HERA_DB) =="
$PY scripts/hera_db.py --init >/dev/null

echo "== e2e_seed_guards[$SUB]: index seed pack =="
$PY scripts/seed_index.py "$PACK" --json >/dev/null

if [ "$SUB" = "prune" ]; then
  # Artificially age ALL seed (pinned) pages far past prune_min_age_days, then
  # confirm they still never appear in the prune candidate band.
  $PY - <<'PY'
import os, sys, pathlib
sys.path.insert(0, "scripts")
import hera_db, prune
conn = hera_db.connect(pathlib.Path(os.environ["HERA_DB"]))

# Push every pinned page's created_at to a date far older than any min-age.
conn.execute("UPDATE pages SET created_at='2000-01-01T00:00:00' WHERE pinned=1")
conn.commit()

npinned = conn.execute("SELECT count(*) FROM pages WHERE pinned=1").fetchone()[0]
print(f"  aged {npinned} pinned seed pages to 2000-01-01")
assert npinned > 0, "no pinned seed pages present — seed index did not pin"

# Run the REAL candidate selection + banding used by /hera-prune.
cs = prune.eligible(conn)
low = int(prune._config(conn, "prune_pct_low", "40"))
high = int(prune._config(conn, "prune_pct_high", "70"))
band = prune.middle_band(cs, low, high)

pinned_in_elig = [c for c in cs
                  if conn.execute("SELECT pinned FROM pages WHERE id=?",
                                  (c.page_id,)).fetchone()[0]]
pinned_in_band = [c for c in band
                  if conn.execute("SELECT pinned FROM pages WHERE id=?",
                                  (c.page_id,)).fetchone()[0]]
print(f"  eligible={len(cs)}  band={len(band)}  "
      f"pinned-in-eligible={len(pinned_in_elig)}  pinned-in-band={len(pinned_in_band)}")

assert len(pinned_in_elig) == 0, \
    f"FAIL: {len(pinned_in_elig)} pinned pages leaked into prune eligibility"
assert len(pinned_in_band) == 0, \
    f"FAIL: {len(pinned_in_band)} pinned pages leaked into prune band"
print("  OK: zero pinned/seed pages are prune candidates")
PY
  # Also exercise the CLI path the skill actually runs, to be doubly sure the
  # printed candidate list contains no seed titles.
  echo "== e2e_seed_guards[prune]: CLI candidates (dry-run) shows no seed =="
  CAND=$($PY scripts/prune.py candidates --json)
  CAND="$CAND" $PY - <<'PY'
import os, json
band = json.loads(os.environ["CAND"])
# Seed pages are the pinned pack rows; none should be printed as candidates.
# We already proved pinned=0 in the SQL above; here we just confirm the JSON
# candidate list did not somehow include one of the well-known seed titles.
titles = {c.get("title") for c in band}
seed_markers = {"Spaced Repetition", "Tidal Locking", "Anki", "SQLite"}
leaked = titles & seed_markers
assert not leaked, f"FAIL: seed titles present in prune candidates: {leaked}"
print(f"  candidate band size={len(band)}; no known seed titles present")
PY
  echo "== e2e_seed_guards[prune]: OK =="
  exit 0
fi

if [ "$SUB" = "conflict" ]; then
  # Drive the REAL ingest_source seed-wins path. We monkeypatch only the LLM
  # EXTRACTION (nondeterministic, expensive) so it yields a single concept whose
  # title slugs to an existing seed page's file. The contradiction DETECTION is
  # the genuine engine call (one small LLM call, as in e2e_conflicts.sh).
  #
  # source_kind="file" (NOT session) — this proves the seed guard fires
  # independently of the ADR-11 session-explicit path.
  TARGET_TITLE="Spaced Repetition"
  NOTE="$TMPDIR_/user_note.txt"
  cat > "$NOTE" <<'TXT'
Correction on spaced repetition: the SM-2 algorithm uses FIVE interval steps
before a card is considered mature, not six. The sixth step was removed from
the reference implementation.
TXT

  $PY - "$TARGET_TITLE" "$NOTE" <<'PY'
import os, sys, pathlib, json
sys.path.insert(0, "scripts")
import hera_db, ingest

target_title = sys.argv[1]
note_path = sys.argv[2]

conn = hera_db.connect(pathlib.Path(os.environ["HERA_DB"]))

# Confirm the seed page exists and is pinned before we contradict it.
target_path = pathlib.Path("wiki/concepts") / f"{ingest._slugify(target_title)}.md"
existing = ingest._existing_page_at(conn, target_path)
assert existing is not None, f"seed page not found at {target_path}"
seed_id, _old_body = existing
pinned = conn.execute("SELECT pinned FROM pages WHERE id=?", (seed_id,)).fetchone()[0]
assert pinned == 1, f"target seed page {seed_id} is not pinned (pinned={pinned})"
print(f"  target seed page id={seed_id} pinned={pinned} path={target_path.as_posix()}")

# Monkeypatch ONLY extraction: return a fixed payload with one concept at the
# seed page's title/path whose body contradicts the seed claim (8 -> 9 categories).
def _fake_extract(raw):
    return {
        "source": {
            "title": "User Correction On SM-2 Interval Steps",
            "one_line": "A user note correcting the SM-2 interval step count.",
            "key_takeaways": ["SM-2 uses five interval steps, not six."],
            "body": raw,
        },
        "concepts": [{
            "title": target_title,
            "one_line": "Scheduling reviews at increasing intervals near the point of forgetting.",
            "body": ("Spaced repetition schedules a review just before predicted "
                     "forgetting. The SM-2 algorithm uses FIVE interval steps "
                     "before a card is considered mature; the sixth step was "
                     "removed from the reference implementation."),
            "aliases": [],
        }],
        "entities": [],
        "warnings": [],
    }

ingest._call_claude_extract = _fake_extract

# Run the real engine as a FILE ingest (not session).
r = ingest.ingest_source(note_path, source_kind="file", conn=conn)
print("  ingest warnings:", r.warnings)

# --- Assertions ---------------------------------------------------------
# 1) No OPEN conflict row for the seed page.
open_for_page = conn.execute(
    "SELECT count(*) FROM conflicts WHERE page_id=? AND status='open'", (seed_id,)
).fetchone()[0]
assert open_for_page == 0, \
    f"FAIL: {open_for_page} open conflict row(s) for seed page (expected 0 — user should win)"

# No open conflicts anywhere, for good measure.
open_all = conn.execute("SELECT count(*) FROM conflicts WHERE status='open'").fetchone()[0]
assert open_all == 0, f"FAIL: {open_all} open conflict(s) present (expected 0)"

# The conflict must have been recorded then auto-resolved as resolved_new.
resolved_new = conn.execute(
    "SELECT count(*) FROM conflicts WHERE page_id=? AND status='resolved_new'", (seed_id,)
).fetchone()[0]
assert resolved_new >= 1, \
    f"FAIL: expected a resolved_new conflict for seed page, got {resolved_new}"

# 2) The page body reflects the USER's new content (user-wins). resolve_new
#    appends an `## Update` block with the new claim and a `## Superseded` block.
body = target_path.read_text(encoding="utf-8")
assert "## Superseded" in body, "FAIL: page missing ## Superseded block (resolve_new not applied)"
assert "## Update" in body, "FAIL: page missing ## Update block (new claim not written)"
low = body.lower()
assert ("five" in low or "sixth step was removed" in low), \
    "FAIL: page body does not carry the user's new claim (not user-wins)"

# 3) The page must NOT be left frozen/stale: still present, not archived.
row = conn.execute("SELECT archived_at, pinned FROM pages WHERE id=?", (seed_id,)).fetchone()
assert row is not None, "FAIL: seed page row vanished"
assert row[0] is None, f"FAIL: seed page was archived (archived_at={row[0]})"

# And no duplicate page row was minted at the same path (id reuse held).
npath = conn.execute("SELECT count(*) FROM pages WHERE path=?",
                     (target_path.as_posix(),)).fetchone()[0]
assert npath == 1, f"FAIL: {npath} page rows at seed path (expected 1 — id reuse broke)"

print("  OK: seed conflict auto-resolved USER-WINS "
      "(0 open conflicts, body updated, page not frozen, single row)")
PY
  echo "== e2e_seed_guards[conflict]: OK =="
  exit 0
fi
