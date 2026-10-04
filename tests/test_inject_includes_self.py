#!/usr/bin/env python3
"""CP-2.5 — the hook still does its job: self-tier pages are injected.

A filter that suppressed everything would pass the exclusion test and be
worthless. This file is the counterweight: with the same real hook, real
search and real embeddings, an operator-authored page relevant to the prompt
must come back, as a resolvable absolute path, with no attribution prefix
(it is the operator's own note, not a teammate's).
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _trust_helpers import REPO, Vault, run_tests  # noqa: E402

HOOK = REPO / ".claude" / "hooks" / "prompt_inject.py"
PY = REPO / ".venv" / "bin" / "python"

PROMPT = "remind me how the greenhouse irrigation schedule works"
TITLE = "Greenhouse Irrigation Schedule"
BODY = ("The greenhouse irrigation schedule waters the beds at dawn and dusk, "
        "with a longer soak on the seedling trays every third day.")


def _run_hook(root: pathlib.Path, prompt: str = PROMPT):
    env = dict(os.environ)
    env["HERA_VAULT"] = str(root)
    env["HERA_DB"] = str(root / "hera.db")
    env["HERA_TEAM_DB"] = str(root / "no-such-team.db")
    env.pop("HERA_OFF", None)
    env.pop("HERA_INJECT_NO_OLLAMA", None)
    p = subprocess.run([str(PY), str(HOOK)],
                       input=json.dumps({"prompt": prompt}),
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, f"hook exited {p.returncode}: {p.stderr}"
    return p.stdout


def test_self_page_is_injected():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page(TITLE, BODY, trust="self")
        out = _run_hook(root)
        assert TITLE in out, f"self-tier page was not injected:\n{out}"
        assert "Relevant vault pages" in out, out


def test_pointer_is_a_wikilink_with_a_resolvable_absolute_path():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page(TITLE, BODY, trust="self")
        out = _run_hook(root)
        line = next(l for l in out.splitlines() if TITLE in l)
        assert f"[[{TITLE}]]" in line, f"not emitted as a wikilink: {line}"
        path = line.split("(", 1)[1].split(")", 1)[0]
        assert pathlib.Path(path).is_absolute(), f"path not absolute: {path}"
        assert pathlib.Path(path).exists(), f"path does not resolve: {path}"


def test_self_page_carries_no_team_attribution():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page(TITLE, BODY, trust="self")
        out = _run_hook(root)
        line = next(l for l in out.splitlines() if TITLE in l)
        assert "(team" not in line, f"own note attributed to a team: {line}"
        assert line.startswith(f"- [[{TITLE}]]"), (
            f"self pointer has an unexpected prefix: {line}")


def test_self_pages_survive_alongside_untrusted_ones():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page(TITLE, BODY, trust="self")
        v.add_page("Greenhouse Irrigation Rumour",
                   BODY + " Forwarded from an unverified mailing list.",
                   trust="untrusted")
        out = _run_hook(root)
        assert TITLE in out, out
        assert "Rumour" not in out, f"untrusted neighbour leaked:\n{out}"


def test_hera_off_still_suppresses_everything():
    """The documented off switch must keep working after the trust change."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        v = Vault(root)
        v.add_page(TITLE, BODY, trust="self")
        env = dict(os.environ)
        env["HERA_VAULT"] = str(root)
        env["HERA_DB"] = str(root / "hera.db")
        env["HERA_OFF"] = "1"
        p = subprocess.run([str(PY), str(HOOK)],
                           input=json.dumps({"prompt": PROMPT}),
                           capture_output=True, text=True, env=env, timeout=120)
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == "", f"HERA_OFF=1 still emitted:\n{p.stdout}"


if __name__ == "__main__":
    sys.exit(run_tests(dict(globals())))


def test_pointer_block_says_how_citations_are_credited():
    """Without this line the model almost never cites (3 citing answers in
    421 real sessions), so the Stop-hook ranking loop never learns."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        Vault(root).add_page(TITLE, BODY, trust="self")
        out = _run_hook(root)
        assert "(Source: [[Title]])" in out.splitlines()[0], out
