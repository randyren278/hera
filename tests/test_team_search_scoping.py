"""team_search --owner scoping, in a throwaway vault (needs the embed endpoint).

Hermetic replacement for the legacy test_team_search.sh, which wrote a fixture
into the live vault's team-staging/ and predated the team.db index (ADR-14).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
UNIQ = "zzquantumfoo"


pytestmark = pytest.mark.needs_ollama


def _page(path: pathlib.Path, pid: str, owner: str, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: {pid}\ntype: concept\ntitle: \"{title}\"\naliases: []\n"
                    f"visibility: public\nowner: {owner}\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def team_vault(tmp_path):
    staging = tmp_path / "team-staging"
    (staging / ".git").mkdir(parents=True)
    _page(staging / "casey/concepts/Quantum Widget.md", "01TESTCASEY0000000000000000",
          "casey", "Quantum Widget", f"The {UNIQ} concept belongs to casey only.")
    _page(staging / "randy/concepts/Randy Retrieval.md", "01TESTRANDY0000000000000000",
          "randy", "Randy Retrieval", "Notes on retrieval by randy.")
    env = dict(os.environ, HERA_VAULT=str(tmp_path), HERA_TEAM_DB=str(tmp_path / "team.db"),
               HERA_DB=str(tmp_path / "hera.db"), HERA_TEAM_REMOTE="")

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([PY, *args], capture_output=True, text=True, env=env,
                              cwd=str(tmp_path))

    r = run(str(REPO / "scripts/team_index.py"), "reindex", "--all")
    assert r.returncode == 0, r.stderr
    return run


def _search(run, *args: str) -> list[dict]:
    r = run(str(REPO / "scripts/team_search.py"), "--json", *args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_search_all_tags_teammate_hit(team_vault):
    hits = _search(team_vault, UNIQ)
    assert any(h["owner"] == "casey" and h["source"] == "team" for h in hits), hits


def test_owner_scope_excludes_other_owners_and_personal(team_vault):
    hits = _search(team_vault, "--owner", "casey", UNIQ)
    assert hits and all(h["source"] == "team" and h["owner"] == "casey" for h in hits), hits
    hits = _search(team_vault, "--owner", "randy", "retrieval")
    assert all(h["source"] == "team" and h["owner"] == "randy" for h in hits), hits


def test_legacy_scheme_team_db_is_fully_reembedded(team_vault, tmp_path):
    """A team.db indexed before the current embedding scheme would never pass
    the injection gate; the next (changed-only) sync rebuilds every vector."""
    sys.path.insert(0, str(REPO / "scripts"))
    import hera_db
    conn = hera_db.connect(tmp_path / "team.db")
    conn.execute("DELETE FROM config WHERE key='embed_scheme'")
    conn.commit()
    r = team_vault(str(REPO / "scripts/team_index.py"), "reindex")  # changed-only
    assert r.returncode == 0 and "indexed 2 " in r.stdout, r.stdout + r.stderr
    import embed
    assert hera_db.embed_scheme(hera_db.connect(tmp_path / "team.db")) == embed.SCHEME
