#!/usr/bin/env python3
"""CP-2.5 — team-tier pointers are delimited with explicit attribution.

A teammate's page is trusted enough to inject but is not the operator's own
words, and the reader has to be able to tell the difference. So the pointer
carries `(team: <owner>)` in FRONT of the wikilink — provenance read before
content, not discovered at the end of the line.

Builds a scratch team.db in the shape team_index.py produces (canonical schema
+ the team-only page_meta table) and runs the real hook against it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_ollama  # real embeddings; see conftest.py

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _trust_helpers import REPO, Vault, run_tests  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import embed as _embed  # noqa: E402
import hera_db  # noqa: E402

HOOK = REPO / ".claude" / "hooks" / "prompt_inject.py"
PY = REPO / ".venv" / "bin" / "python"

PROMPT = "what does anyone know about the sourdough starter hydration ratio"
TEAM_TITLE = "Sourdough Starter Hydration"
TEAM_BODY = ("The sourdough starter hydration ratio is kept at 100% by weight, "
             "refreshed twice daily during a build.")
OWNER = "alice"


def _build_team_db(root: pathlib.Path, trust: str = "team") -> pathlib.Path:
    """A team.db in team_index.py's shape, holding one page owned by `alice`."""
    db = root / "team.db"
    conn = hera_db.ensure_ready(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS page_meta (
        page_id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL,
        rel_path TEXT NOT NULL, mtime REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id))""")

    rel = f"team-staging/{OWNER}/{TEAM_TITLE}.md"
    page = root / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ntitle: \"{TEAM_TITLE}\"\n---\n\n{TEAM_BODY}\n",
                    encoding="utf-8")

    pid, now = "01TEAMPAGE0001", time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO pages(id,title,aliases,type,path,created_at,updated_at,trust) "
        "VALUES (?,?,'[]','concept',?,?,?,?)", (pid, TEAM_TITLE, rel, now, now, trust))
    cur = conn.execute("INSERT INTO pages_fts(title, body) VALUES (?, ?)",
                       (TEAM_TITLE, TEAM_BODY))
    conn.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)",
                 (cur.lastrowid, pid))
    vec = _embed.embed(f"{TEAM_TITLE}\n{TEAM_BODY}")
    conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
                 (pid, _embed.pack(vec)))
    conn.execute("INSERT INTO page_meta VALUES (?,?,'team',?,?)",
                 (pid, OWNER, rel, page.stat().st_mtime))
    conn.commit()
    return db


def _run_hook(root: pathlib.Path, team_db: pathlib.Path) -> str:
    env = dict(os.environ)
    env["HERA_VAULT"] = str(root)
    env["HERA_DB"] = str(root / "hera.db")
    env["HERA_TEAM_DB"] = str(team_db)
    env.pop("HERA_OFF", None)
    env.pop("HERA_INJECT_NO_OLLAMA", None)
    p = subprocess.run([str(PY), str(HOOK)],
                       input=json.dumps({"prompt": PROMPT}),
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, f"hook exited {p.returncode}: {p.stderr}"
    return p.stdout


def _team_line(out: str) -> str:
    matches = [l for l in out.splitlines() if TEAM_TITLE in l]
    assert matches, f"the team page was not injected at all:\n{out}"
    return matches[0]


def test_team_page_is_injected():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        Vault(root)
        out = _run_hook(root, _build_team_db(root))
        assert TEAM_TITLE in out, out


def test_attribution_names_the_owner():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        Vault(root)
        line = _team_line(_run_hook(root, _build_team_db(root)))
        assert f"(team: {OWNER})" in line, f"no owner attribution: {line}"


def test_attribution_precedes_the_content():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        Vault(root)
        line = _team_line(_run_hook(root, _build_team_db(root)))
        assert line.startswith(f"- (team: {OWNER}) "), (
            f"attribution is not at the front of the pointer: {line}")
        assert line.index(f"(team: {OWNER})") < line.index(f"[[{TEAM_TITLE}]]"), (
            f"attribution appears after the title: {line}")


def test_own_pages_are_not_attributed_to_the_team():
    """Attribution must distinguish, not decorate: a local self page in the
    same output carries no team marker."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page("Sourdough Starter Notes",
                   "My own notes on sourdough starter hydration and feeding.",
                   trust="self")
        out = _run_hook(root, _build_team_db(root))
        own = [l for l in out.splitlines() if "Sourdough Starter Notes" in l]
        assert own, f"local self page missing from output:\n{out}"
        assert "(team" not in own[0], f"own note attributed to a team: {own[0]}"


def test_untrusted_team_row_is_not_injected():
    """team.db is supposed to hold only team-tier content. If a row in it is
    ever marked untrusted, the hook must still refuse it."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        Vault(root)
        out = _run_hook(root, _build_team_db(root, trust="untrusted"))
        assert TEAM_TITLE not in out, (
            f"an untrusted row in team.db was injected:\n{out}")


if __name__ == "__main__":
    sys.exit(run_tests(dict(globals())))
