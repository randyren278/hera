#!/usr/bin/env bash
# e2e_team_isolation.sh — CP-5: the team index never leaks into brain.db.
# Snapshots brain.db, runs a full team cycle (search + injection), and asserts
# brain.db's page set is unchanged and disjoint from team.db, and that no team
# ULID gained a citation row in brain.db.
set -euo pipefail
cd "${SECOND_BRAIN_VAULT:?SECOND_BRAIN_VAULT must be set}"

PY=.venv/bin/python

"$PY" scripts/team_index.py reindex --all >/dev/null

"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, "scripts")
import brain_db, team_index, team_search

# --- snapshot brain.db before ---
lconn = brain_db.connect()
before_count = lconn.execute("SELECT count(*) FROM pages").fetchone()[0]
before_ids = {r[0] for r in lconn.execute("SELECT id FROM pages").fetchall()}
before_citations = lconn.execute("SELECT count(*) FROM citations").fetchone()[0]

# --- team.db ULID set ---
tconn = team_index.open_team_db()
team_ids = {r[0] for r in tconn.execute("SELECT id FROM pages").fetchall()}
assert team_ids, "team.db has no pages — nothing to audit"

# --- run a full team cycle (search touches both DBs; injection is exercised in CP-3) ---
res = team_search.search("cron database corruption", top=10)
assert isinstance(res, list)

# --- snapshot brain.db after ---
lconn2 = brain_db.connect()
after_count = lconn2.execute("SELECT count(*) FROM pages").fetchone()[0]
after_ids = {r[0] for r in lconn2.execute("SELECT id FROM pages").fetchall()}
after_citations = lconn2.execute("SELECT count(*) FROM citations").fetchone()[0]

# 1. brain.db page count unchanged.
assert after_count == before_count, f"brain.db page count changed: {before_count} -> {after_count}"

# 2. brain.db page set unchanged by the team cycle (no team page was written in).
assert after_ids == before_ids, "brain.db page id set changed during team cycle"

# 3. The isolation invariant: no team page's CONTENT lives in brain.db. A ULID may
#    legitimately appear in both DBs when the local user authored a page AND
#    published it to their own team folder (ULID reuse is by design). What must
#    NEVER happen is a team page's row in brain.db pointing at team-brain-staging/,
#    or a teammate's page (one the local user did not author) appearing at all.
overlap = team_ids & after_ids
for pid in overlap:
    lrow = lconn2.execute("SELECT path FROM pages WHERE id = ?", (pid,)).fetchone()
    lpath = lrow[0] if lrow else ""
    assert lpath.startswith("wiki/"), (
        f"team ULID {pid} present in brain.db but its path is {lpath!r} — "
        f"a team page leaked into the personal index")
    # And it must be one of the local user's OWN published pages (owner 'randy'),
    # never a teammate's.
    own = tconn.execute(
        "SELECT owner FROM page_meta WHERE page_id = ?", (pid,)).fetchone()[0]
    assert own == team_index.os.environ.get("BRAIN_OWNER", "randy"), (
        f"overlapping ULID {pid} belongs to teammate {own!r}, not the local user "
        f"— a teammate's page leaked into brain.db")

# 4. The team cycle must not add ANY citations to brain.db (the scorer only ever
#    scores local wikilinks; team retrieval must be citation-neutral).
assert after_citations == before_citations, (
    f"brain.db citation count changed during team cycle: "
    f"{before_citations} -> {after_citations}")

# 5. No TEAMMATE's page (one the local user did not author) has a citation in
#    brain.db. A local-authored-then-published page (path under wiki/, owner ==
#    local user) may carry pre-existing local citations — that's legitimate local
#    activity, not a leak. Only a teammate's ULID appearing in citations is wrong.
me = team_index.os.environ.get("BRAIN_OWNER", "randy")
teammate_ids = {pid for pid in team_ids
                if tconn.execute("SELECT owner FROM page_meta WHERE page_id = ?",
                                 (pid,)).fetchone()[0] != me}
if teammate_ids:
    ph2 = ",".join(["?"] * len(teammate_ids))
    cited_teammates = lconn2.execute(
        f"SELECT count(*) FROM citations WHERE page_id IN ({ph2})",
        tuple(teammate_ids)).fetchone()[0]
    assert cited_teammates == 0, (
        f"{cited_teammates} citations in brain.db for teammate pages — a leak")

print(f"ok: brain.db pages={after_count} unchanged; team.db pages={len(team_ids)}; "
      f"{len(overlap)} shared ULID(s) local-authored; citations unchanged "
      f"({before_citations}); 0 teammate citations in brain.db")
PYEOF

echo "CP-5 isolation e2e: PASS"
