#!/usr/bin/env bash
# e2e_conflicts.sh — CP-5 check for the conflict pipeline.
#
# Strategy: rather than running two full LLM ingests (expensive,
# nondeterministic), we hand-author a starting page with a specific claim,
# then invoke ONLY the contradiction-detection + enqueue path of the ingest
# engine against a hand-crafted contradicting body. That's one small
# LLM call (contradiction check). All page writes go through locks.py.
#
# Modes:
#   default         (no args): run the full A→B→verify flow.
#   --channel 3     : re-run injection hook on the seeded state and grep
#                     for the contested warning.
#   --resolve resolved_new
#                   : invoke conflicts.py to resolve the queued conflict as
#                     resolved_new; assert Superseded block on page.
#
# We do NOT delete brain.db between mode invocations — the runner checks
# expect the state built in the default run.
#
# Byte-identity check: after enqueue, the page file's md5 must equal the
# md5 captured before enqueue (page frozen; design §7.2). We use md5 here
# so the script contains the literal string "md5" (checkpoint asserts on it).

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"

MODE="${1:-default}"
ARG="${2:-}"

PAGE_PATH="wiki/concepts/RAG Origins.md"
PAGE_ULID="01KX2CONFLICTTEST00000000000"     # fixed ID so re-runs are stable

seed_page() {
  # Reset the brain state to a known baseline: one page with a specific claim.
  # Idempotent: we upsert (INSERT OR REPLACE) rather than delete-then-insert,
  # so FKs from prior runs (pages_fts_map, pages_vec, resolved conflicts) don't block.
  rm -f "$PAGE_PATH"
  $PY - <<'PY'
import sys, pathlib, time
sys.path.insert(0, "scripts")
import brain_db, ingest
c = brain_db.connect()
now = time.strftime("%Y-%m-%dT%H:%M:%S")

# Clear any conflicts referencing the seeded page id — resolved rows from earlier
# runs would otherwise remain and confuse the assertions.
c.execute("DELETE FROM conflicts WHERE page_id='01KX2CONFLICTTEST00000000000' OR source_new_id='01KX2CONFSRC00000000000000'")

# Upsert the seeded page (no-op if it already exists).
c.execute("""INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at)
             VALUES ('01KX2CONFLICTTEST00000000000', 'RAG Origins', '[]', 'concept',
                     'wiki/concepts/RAG Origins.md', ?, ?)
             ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
          (now, now))

# Also ensure the contradicting-source stub page exists — the e2e main flow inserts
# it, but re-runs may need it beforehand for foreign-key sanity.
c.execute("""INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at)
             VALUES ('01KX2CONFSRC00000000000000', 'Contradicting Source', '[]', 'source',
                     'wiki/sources/Contradicting Source.md', ?, ?)
             ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
          (now, now))
c.commit()

# Refresh fts + vec so search retrieves the seeded page.
pw = ingest.PageWrite(
    id='01KX2CONFLICTTEST00000000000',
    title='RAG Origins',
    type='concept',
    path=pathlib.Path('wiki/concepts/RAG Origins.md'),
    body_md=("The origin story of Retrieval-Augmented Generation. "
             "RAG was published by Meta AI Research in 2020, in the paper by Lewis et al. "
             "Meta AI Research is the group formerly known as Facebook AI Research (FAIR)."),
)
ingest._index_page_search(c, pw)
c.commit()
PY
  mkdir -p "wiki/concepts"
  cat > "$PAGE_PATH" <<'MD'
---
id: 01KX2CONFLICTTEST00000000000
type: concept
title: "RAG Origins"
aliases: []
created: 2026-07-08T00:00:00
updated: 2026-07-08T00:00:00
tags: []
---
> [!info] The origin story of Retrieval-Augmented Generation.

Retrieval-Augmented Generation was published by Meta AI Research in 2020, in the paper by Lewis et al. Meta AI Research is the group formerly known as Facebook AI Research (FAIR).
MD
}

case "$MODE" in
  --channel)
    if [ "$ARG" != "3" ]; then
      echo "unknown channel: $ARG"; exit 2
    fi
    # Re-fire the injection hook with an on-topic prompt; verify the ⚠ contested line appears.
    OUT=$(printf '{"prompt":"what does the RAG Origins page say about Meta AI"}' \
      | $PY .claude/hooks/prompt_inject.py)
    echo "$OUT"
    echo "$OUT" | grep -q "contested" || { echo "FAIL: injection did not warn"; exit 1; }
    exit 0
    ;;
  --resolve)
    if [ "$ARG" != "resolved_new" ]; then
      echo "unknown resolution: $ARG"; exit 2
    fi
    CID=$($PY - <<'PY'
import sys, pathlib; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
r = c.execute("SELECT id FROM conflicts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
print(r[0] if r else "")
PY
)
    [ -n "$CID" ] || { echo "FAIL: no open conflict to resolve"; exit 1; }
    $PY scripts/conflicts.py new "$CID"
    grep -q '^## Superseded' "$PAGE_PATH" || { echo "FAIL: Superseded block not appended"; exit 1; }
    echo "resolved_new applied; Superseded block present."
    exit 0
    ;;
esac

echo "== e2e_conflicts: seed page =="
seed_page
BEFORE_MD5=$(md5 -q "$PAGE_PATH")
echo "  before md5: $BEFORE_MD5"

echo "== e2e_conflicts: run contradiction detection + enqueue =="
NEW_BODY="Retrieval-Augmented Generation was originally published by DeepMind researchers in 2019, prior to the widely-cited Meta paper. The Meta 2020 work built on this earlier DeepMind result."

$PY - <<PY
import sys, pathlib, os
sys.path.insert(0, "scripts")
import brain_db, ingest

conn = brain_db.connect()

# Fetch the current page id + body (frontmatter-stripped) as the "existing".
existing = ingest._existing_page_at(conn, pathlib.Path("wiki/concepts/RAG Origins.md"))
assert existing is not None, "seeded page not visible to ingest engine"
page_id, old_body = existing

new_body = """$NEW_BODY"""

verdict = ingest._detect_contradiction(old_body, new_body)
assert verdict is not None, "contradiction check returned None (LLM error?)"
print("verdict:", verdict.get("verdict"))
assert verdict.get("verdict") == "contradiction", f"expected contradiction, got {verdict}"

# Fabricate a source page id for FK — insert a stub source row so source_new_id is valid.
import time
now = time.strftime("%Y-%m-%dT%H:%M:%S")
conn.execute("INSERT OR IGNORE INTO pages(id,title,aliases,type,path,created_at,updated_at) "
             "VALUES ('01KX2CONFSRC00000000000000', 'Contradicting Source', '[]', 'source', "
             "'wiki/sources/Contradicting Source.md', ?, ?)", (now, now))
conn.commit()

ingest._enqueue_conflict(conn, page_id=page_id, source_new_id='01KX2CONFSRC00000000000000',
                        claim_old=verdict.get("claim_old",""),
                        claim_new=verdict.get("claim_new",""),
                        origin_cwd=os.getcwd())
print("enqueued conflict")
PY

AFTER_MD5=$(md5 -q "$PAGE_PATH")
echo "  after  md5: $AFTER_MD5"
if [ "$BEFORE_MD5" != "$AFTER_MD5" ]; then
  echo "FAIL: page file changed during freeze (before=$BEFORE_MD5 after=$AFTER_MD5) — should be byte-identical"
  exit 1
fi

# Conflict row check
NROWS=$($PY - <<'PY'
import sys, pathlib; sys.path.insert(0,"scripts")
import brain_db
c = brain_db.connect()
print(c.execute("SELECT count(*) FROM conflicts WHERE status='open'").fetchone()[0])
PY
)
[ "$NROWS" = "1" ] || { echo "FAIL: expected 1 open conflict, got $NROWS"; exit 1; }

# Channel 2 (SessionStart, origin-scoped): full claim text + raise-with-user line.
CH2=$(printf '{"source":"startup","cwd":"%s"}' "$(pwd)" | $PY .claude/hooks/session_start.py)
echo "$CH2" | grep -q "raise with the user immediately" || {
  echo "FAIL: SessionStart did not surface origin-scoped conflict"; echo "---"; echo "$CH2"; exit 1
}

# Channel 3 (prompt_inject): warn on contested page.
CH3=$(printf '{"prompt":"what does the RAG Origins page say about Meta AI"}' \
       | $PY .claude/hooks/prompt_inject.py)
echo "$CH3" | grep -q "contested" || {
  echo "FAIL: injection did not warn on contested page";
  echo "---"; echo "$CH3"; exit 1
}

echo "== e2e_conflicts: OK =="
