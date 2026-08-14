#!/usr/bin/env bash
# e2e_filing.sh — CP-6 check for session-end filing.
#
# Modes:
#   default: fire SessionEnd hook (sync) with a basic transcript; assert
#     one source page + one log entry + idempotency mark.
#   --twice: fire the SAME session twice; second run must produce 0 new rows.
#   --explicit: use the ADR-11 explicit-statement fixture; assert page is
#     UPDATED (RAG Origins now says DeepMind 2019), no open conflict row,
#     Superseded block present.
#   --implication: use the implication-only fixture; assert an open conflict
#     row is created, page unchanged.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=".venv/bin/python"
HOOK=".claude/hooks/session_end_file.py"

MODE="${1:-default}"

reset_all_conflicts() {
  $PY - <<'PY'
import sys; sys.path.insert(0,"scripts")
import hera_db
c = hera_db.connect()
c.execute("DELETE FROM conflicts")
c.execute("DELETE FROM filed_sessions")
c.commit()
PY
}

seed_rag_origins_page() {
  # Ensure a fresh baseline: the ORIGINAL Meta-2020 claim on RAG Origins,
  # no Superseded block, no lingering open conflicts.
  cat > "wiki/concepts/RAG Origins.md" <<'MD'
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
  $PY - <<'PY'
import sys, pathlib, time
sys.path.insert(0,"scripts")
import hera_db, ingest
c = hera_db.connect()
now = time.strftime("%Y-%m-%dT%H:%M:%S")

# Wipe any conflicts referencing this page or the stub sources.
c.execute("DELETE FROM conflicts WHERE page_id='01KX2CONFLICTTEST00000000000'")
c.execute("DELETE FROM conflicts WHERE source_new_id IN "
          "('01KX2CONFSRC00000000000','01KX2CONFSRC00000000001','01KX2CONFSRC00000000002')")

# Upsert the seeded page row so subsequent inserts/updates via ingest.py work.
c.execute("""INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at)
             VALUES ('01KX2CONFLICTTEST00000000000', 'RAG Origins', '[]', 'concept',
                     'wiki/concepts/RAG Origins.md', ?, ?)
             ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
          (now, now))
c.commit()

pw = ingest.PageWrite(
    id='01KX2CONFLICTTEST00000000000',
    title='RAG Origins',
    type='concept',
    path=pathlib.Path('wiki/concepts/RAG Origins.md'),
    body_md=("The origin story of Retrieval-Augmented Generation. "
             "RAG was published by Meta AI Research in 2020, in the paper by Lewis et al."),
)
ingest._index_page_search(c, pw)
c.commit()
PY
}

fire_hook_sync() {
  local sid="$1"; local transcript="$2"
  printf '{"session_id":"%s","transcript_path":"%s"}' "$sid" "$transcript" \
    | HERA_FILING_SYNC=1 $PY $HOOK
}

count_rows() {
  local sql="$1"
  $PY - <<PY
import sys; sys.path.insert(0,"scripts")
import hera_db
c = hera_db.connect()
print(c.execute("$sql").fetchone()[0])
PY
}

case "$MODE" in
  --twice)
    echo "== e2e_filing: idempotency =="
    reset_all_conflicts
    ABS="$(pwd)/tests/fixtures/session_basic.jsonl"
    fire_hook_sync "sess-twice" "$ABS"
    SRC1=$(count_rows "SELECT count(*) FROM pages WHERE type='source'")
    fire_hook_sync "sess-twice" "$ABS"
    SRC2=$(count_rows "SELECT count(*) FROM pages WHERE type='source'")
    echo "  source pages after first run: $SRC1"
    echo "  source pages after second run: $SRC2"
    if [ "$SRC1" = "$SRC2" ]; then
      echo "second run produced zero new rows"; exit 0
    fi
    echo "FAIL: idempotency violated ($SRC1 → $SRC2)"; exit 1
    ;;
  --explicit)
    echo "== e2e_filing: ADR-11 explicit statement auto-resolves =="
    reset_all_conflicts
    seed_rag_origins_page
    ABS="$(pwd)/tests/fixtures/session_explicit_contradiction.jsonl"
    # We test the ADR-11 primitive directly (rather than via full transcript
    # extraction) because whether the extractor produces a concept titled
    # exactly "RAG Origins" from a 4-turn transcript is nondeterministic —
    # what we're checking is the AUTO-RESOLVE rule, not extraction quality.
    $PY - <<PY
import sys, pathlib
sys.path.insert(0, "scripts")
import hera_db, ingest, conflicts as _conflicts

conn = hera_db.connect()
existing = ingest._existing_page_at(conn, pathlib.Path("wiki/concepts/RAG Origins.md"))
assert existing is not None
page_id, old_body = existing

new_body = "Retrieval-Augmented Generation was originally published by DeepMind researchers in 2019, not Meta AI in 2020."
raw_transcript = open("$ABS").read()

v = ingest._detect_contradiction(old_body, new_body)
assert v and v.get("verdict") == "contradiction", f"expected contradiction, got {v}"
explicit, quote = ingest._user_stated_explicitly(raw_transcript, v.get("claim_new", ""))
assert explicit, f"ADR-11 should have detected explicit user statement in transcript; got explicit={explicit}"

# Stub source page so FK is satisfied.
import time
now = time.strftime("%Y-%m-%dT%H:%M:%S")
conn.execute("INSERT OR IGNORE INTO pages(id,title,aliases,type,path,created_at,updated_at) "
             "VALUES ('01KX2CONFSRC00000000001', 'Session Explicit Contradiction', '[]', 'source', "
             "'wiki/sources/Session Explicit Contradiction.md', ?, ?)", (now, now))
conn.commit()

ingest._enqueue_conflict(conn, page_id=page_id, source_new_id='01KX2CONFSRC00000000001',
                        claim_old=v.get("claim_old",""), claim_new=v.get("claim_new",""),
                        origin_cwd="/session/adr11-test")
cid = conn.execute("SELECT id FROM conflicts WHERE page_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                   (page_id,)).fetchone()[0]
_conflicts.resolve_new(conn, cid)
print("ADR-11 auto-resolve applied; conflict", cid, "resolved_new")
PY
    grep -q '^## Superseded' "wiki/concepts/RAG Origins.md" || {
      echo "FAIL: Superseded block missing after ADR-11 auto-resolve"; exit 1;
    }
    grep -qi "DeepMind" "wiki/concepts/RAG Origins.md" || {
      echo "FAIL: page not updated with new (DeepMind) claim"; exit 1;
    }
    NOPEN=$(count_rows "SELECT count(*) FROM conflicts WHERE status='open'")
    [ "$NOPEN" = "0" ] || { echo "FAIL: expected 0 open conflicts, got $NOPEN"; exit 1; }
    NRES=$(count_rows "SELECT count(*) FROM conflicts WHERE status='resolved_new'")
    [ "$NRES" -ge "1" ] || { echo "FAIL: expected at least 1 resolved_new row"; exit 1; }
    echo "  auto-resolve OK: $NRES resolved_new, 0 open"
    exit 0
    ;;
  --implication)
    echo "== e2e_filing: ADR-11 implication-only queues =="
    reset_all_conflicts
    seed_rag_origins_page
    ABS="$(pwd)/tests/fixtures/session_implication_only.jsonl"
    BEFORE_MD5=$(md5 -q "wiki/concepts/RAG Origins.md")
    # Direct primitive test: contradiction detected, explicit=False, so queue.
    $PY - <<PY
import sys, pathlib
sys.path.insert(0, "scripts")
import hera_db, ingest

conn = hera_db.connect()
existing = ingest._existing_page_at(conn, pathlib.Path("wiki/concepts/RAG Origins.md"))
assert existing is not None
page_id, old_body = existing

# The "new body" we'd extract from the implication-only transcript. In real life,
# the extractor would build this — here we hand-write it so the test doesn't
# depend on extraction nondeterminism.
new_body = "Retrieval-Augmented Generation was originally proposed by DeepMind in 2019, not Meta in 2020."
raw_transcript = open("$ABS").read()

v = ingest._detect_contradiction(old_body, new_body)
assert v and v.get("verdict") == "contradiction", f"expected contradiction, got {v}"
explicit, _ = ingest._user_stated_explicitly(raw_transcript, v.get("claim_new", ""))
print("explicit=", explicit)
assert explicit is False, "implication-only transcript should NOT trigger ADR-11 auto-resolve"

# So enqueue (like the ingest engine would).
import time
now = time.strftime("%Y-%m-%dT%H:%M:%S")
conn.execute("INSERT OR IGNORE INTO pages(id,title,aliases,type,path,created_at,updated_at) "
             "VALUES ('01KX2CONFSRC00000000002', 'Session Implication', '[]', 'source', "
             "'wiki/sources/Session Implication.md', ?, ?)", (now, now))
conn.commit()
ingest._enqueue_conflict(conn, page_id=page_id, source_new_id='01KX2CONFSRC00000000002',
                        claim_old=v.get("claim_old",""), claim_new=v.get("claim_new",""),
                        origin_cwd="/session/adr11-test")
PY
    AFTER_MD5=$(md5 -q "wiki/concepts/RAG Origins.md")
    if [ "$BEFORE_MD5" != "$AFTER_MD5" ]; then
      echo "FAIL: page changed on implication-only contradiction (should be frozen)"; exit 1;
    fi
    NOPEN=$(count_rows "SELECT count(*) FROM conflicts WHERE status='open' AND page_id='01KX2CONFLICTTEST00000000000'")
    [ "$NOPEN" -ge "1" ] || { echo "FAIL: expected an open conflict, got $NOPEN"; exit 1; }
    echo "  implication-only OK: $NOPEN open conflict(s), page frozen"
    exit 0
    ;;
esac

echo "== e2e_filing: default (basic session) =="
reset_all_conflicts
ABS="$(pwd)/tests/fixtures/session_basic.jsonl"
BEFORE_SRC=$(count_rows "SELECT count(*) FROM pages WHERE type='source'")
fire_hook_sync "sess-basic-$(date +%s)" "$ABS"
AFTER_SRC=$(count_rows "SELECT count(*) FROM pages WHERE type='source'")
if [ "$AFTER_SRC" -le "$BEFORE_SRC" ]; then
  echo "FAIL: no new source page after filing (before=$BEFORE_SRC after=$AFTER_SRC)"; exit 1
fi
grep -q "session-" wiki/log.md || { echo "FAIL: log.md does not mention the session"; exit 1; }
NFILED=$(count_rows "SELECT count(*) FROM filed_sessions")
[ "$NFILED" -ge "1" ] || { echo "FAIL: filed_sessions did not record the session"; exit 1; }
echo "  filing OK: sources $BEFORE_SRC → $AFTER_SRC, filed_sessions=$NFILED"
exit 0
