#!/usr/bin/env bash
# e2e_sync_reindex.sh — CP-4: a page arriving via sync gets indexed into team.db
# WITHOUT any manual reindex call. Simulates the post-pull on-disk state, then
# invokes the exact reindex hook that clone_or_pull() runs on a successful sync.
set -euo pipefail
cd "${SECOND_BRAIN_VAULT:?SECOND_BRAIN_VAULT must be set}"

PY=.venv/bin/python
NEWDIR="team-brain-staging/naman/concepts"
NEWFILE="$NEWDIR/E2E Sync Probe Page.md"
PROBE_ID="01KXSYNCPROBE0000000000TEST"

cleanup() {
  rm -f "$NEWFILE"
  # Drop the probe page from team.db so the index reflects the removed file.
  "$PY" scripts/team_index.py reindex --all >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Establish a baseline index.
"$PY" scripts/team_index.py reindex --all >/dev/null

# Simulate a new page landing via `git pull` (i.e. it's now on disk).
mkdir -p "$NEWDIR"
cat > "$NEWFILE" <<EOF
---
id: $PROBE_ID
type: concept
title: "E2E Sync Probe Page"
aliases: []
created: 2026-07-14T00:00:00
updated: 2026-07-14T00:00:00
tags: []
visibility: public
owner: naman
---
> [!info] A synthetic probe page used to verify reindex-on-sync.

This page exists only to confirm that team_sync's post-pull reindex hook
picks up newly-synced Markdown and writes it into team.db automatically.
EOF

# Invoke the SAME hook clone_or_pull() calls on a successful sync — no manual
# team_index reindex here.
"$PY" - <<PYEOF
import sys
sys.path.insert(0, "scripts")
import team_sync
team_sync._reindex_after_sync()
PYEOF

# Assert the probe ULID is now in team.db.
FOUND=$("$PY" - <<PYEOF
import sys
sys.path.insert(0, "scripts")
import team_index
conn = team_index.open_team_db()
row = conn.execute("SELECT 1 FROM pages WHERE id = ?", ("$PROBE_ID",)).fetchone()
vec = conn.execute("SELECT count(*) FROM pages_vec WHERE page_id = ?", ("$PROBE_ID",)).fetchone()[0]
print("yes" if (row and vec == 1) else "no")
PYEOF
)

[ "$FOUND" = "yes" ] || { echo "FAIL: probe page not indexed after sync hook"; exit 1; }
echo "ok: newly-synced page indexed automatically (id=$PROBE_ID)"
echo "CP-4 e2e: PASS"
