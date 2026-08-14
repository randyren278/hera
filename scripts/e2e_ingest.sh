#!/usr/bin/env bash
# e2e_ingest.sh — CP-2 end-to-end check for the ingest pipeline.
#
# Wipes wiki/ and hera.db, runs one ingest against
# tests/fixtures/sample_article.md, then asserts every invariant the
# checkpoint depends on. Property-based (not exact-string) because the
# LLM extraction is nondeterministic — we check that pages exist, have
# ULIDs, DB rows are consistent, and hot/index/log were updated.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PY=".venv/bin/python"
FIXTURE="tests/fixtures/sample_article.md"

echo "== e2e_ingest: reset =="
rm -rf wiki hera.db hera.db-wal hera.db-shm .hera
$PY scripts/hera_db.py --init >/dev/null

echo "== e2e_ingest: run =="
$PY scripts/ingest.py "$FIXTURE" --json > /tmp/e2e_ingest.json
echo "  extractor output:"
sed 's/^/    /' /tmp/e2e_ingest.json

echo "== e2e_ingest: assertions =="

# 1) source page exists on disk under wiki/sources/ and has a ULID.
SRC_PATH="$($PY - <<'PY'
import json
d = json.load(open("/tmp/e2e_ingest.json"))
print(d["source"]["path"])
PY
)"
test -s "$SRC_PATH" || { echo "FAIL: source page missing: $SRC_PATH"; exit 1; }
$PY - <<PY
import re, sys
s = open("$SRC_PATH").read()
m = re.search(r'^id:\s*([0-9A-HJKMNP-TV-Z]{26})', s, re.M)
sys.exit(0 if m else 1)
PY

# 2) At least one concept OR entity page created — the fixture has both people
#    and concepts, and Opus should certainly produce at least one of each,
#    but we relax to >=1 total to avoid pinning to model behaviour.
COUNT=$($PY - <<'PY'
import json
d = json.load(open("/tmp/e2e_ingest.json"))
print(len(d["concepts"]) + len(d["entities"]))
PY
)
[ "$COUNT" -ge 1 ] || { echo "FAIL: no concept or entity pages created"; exit 1; }

# 3) All emitted paths exist on disk and have ULID frontmatter.
$PY - <<'PY'
import json, re, sys
d = json.load(open("/tmp/e2e_ingest.json"))
for p in [d["source"]] + d["concepts"] + d["entities"]:
    s = open(p["path"]).read()
    assert re.search(r"^id:\s*([0-9A-HJKMNP-TV-Z]{26})", s, re.M), f"no ULID in {p['path']}"
    assert re.search(r'^title:', s, re.M), f"no title in {p['path']}"
print("all pages have ULIDs and titles")
PY

# 4) hot.md / index.md / log.md all exist and non-empty and reference the source.
test -s wiki/hot.md   || { echo "FAIL: hot.md empty";   exit 1; }
test -s wiki/index.md || { echo "FAIL: index.md empty"; exit 1; }
test -s wiki/log.md   || { echo "FAIL: log.md empty";   exit 1; }
grep -qF "$($PY -c "import json;print(json.load(open('/tmp/e2e_ingest.json'))['source']['title'])")" wiki/log.md \
  || { echo "FAIL: log.md does not mention source title"; exit 1; }
grep -qF "sample_article" wiki/log.md \
  || { echo "FAIL: log.md does not mention source filename"; exit 1; }

# 5) DB rows: pages, pages_fts, pages_vec all populated to matching counts.
$PY - <<'PY'
import sys, pathlib
sys.path.insert(0, "scripts")
import hera_db
c = hera_db.connect(pathlib.Path("hera.db"))
np = c.execute("select count(*) from pages").fetchone()[0]
nf = c.execute("select count(*) from pages_fts").fetchone()[0]
nv = c.execute("select count(*) from pages_vec").fetchone()[0]
assert np > 0, "pages empty"
assert nf == np, f"fts count {nf} != pages {np}"
assert nv == np, f"vec count {nv} != pages {np}"
print(f"db rows: pages={np} fts={nf} vec={nv}")
PY

# 6) doctor still clean.
$PY scripts/hera_db.py --doctor >/dev/null || { echo "FAIL: doctor not clean"; exit 1; }

# 7) raw source preserved.
test -s wiki/.raw/articles/sample_article.md || { echo "FAIL: raw not preserved"; exit 1; }

echo "== e2e_ingest: OK =="
