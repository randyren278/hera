"""Team publishing safety, enforced in code rather than only in skill text."""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def team(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    staging = tmp_path / "vault" / "team-staging"
    staging.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=staging)
    _git("remote", "add", "origin", str(remote), cwd=staging)
    _git("config", "user.email", "t@t", cwd=staging)
    _git("config", "user.name", "t", cwd=staging)
    (staging / "README.md").write_text("team\n")
    _git("add", "-A", cwd=staging)
    _git("commit", "-qm", "init", cwd=staging)
    _git("push", "-q", "-u", "origin", "main", cwd=staging)
    home = tmp_path / "claude_home"
    home.mkdir()
    env = {**os.environ, "HERA_VAULT": str(tmp_path / "vault"), "CLAUDE_HOME": str(home),
           "HERA_OWNER": "alice", "HERA_TEAM_REMOTE": str(remote)}

    def run(script, *args, **extra):
        return subprocess.run([PY, str(REPO / "scripts" / script), *args], capture_output=True,
                              text=True, env={**env, **extra}, cwd=str(tmp_path))
    return staging, remote, run


def _token(out: str) -> str:
    m = re.search(r"review-token: (\w+)", out)
    assert m, out
    return m.group(1)


def _remote_log(remote):
    return subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline", "main"],
                          capture_output=True, text=True).stdout


def test_push_requires_the_token_of_the_reviewed_diff(team):
    staging, remote, run = team
    page = staging / "alice" / "concepts" / "Idea.md"
    page.parent.mkdir(parents=True)
    page.write_text("A public idea.\n")
    token = _token(run("publish.py", "diff").stdout)

    assert run("publish.py", "push", "-m", "x").returncode != 0          # no token
    assert run("publish.py", "push", "--confirm", "deadbeef0000").returncode != 0
    page.write_text("A public idea, edited after review.\n")             # diff changed
    assert run("publish.py", "push", "--confirm", token).returncode != 0
    assert "init" in _remote_log(remote) and "x" not in _remote_log(remote).split("\n")[0]

    token = _token(run("publish.py", "diff").stdout)
    r = run("publish.py", "push", "-m", "publish idea", "--confirm", token)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "publish idea" in _remote_log(remote)


def test_secrets_block_the_push_even_with_a_token(team):
    staging, remote, run = team
    page = staging / "alice" / "concepts" / "Leak.md"
    page.parent.mkdir(parents=True)
    page.write_text("key: AKIAABCDEFGHIJKLMNOP and ghp_" + "a" * 36 + "\n")
    out = run("publish.py", "diff").stdout
    assert "BLOCKED" in out
    r = run("publish.py", "push", "--confirm", _token(out))
    assert r.returncode != 0 and "secret" in (r.stdout + r.stderr).lower()
    assert "Leak" not in subprocess.run(["git", "--git-dir", str(remote), "log", "--stat", "main"],
                                        capture_output=True, text=True).stdout


def test_no_owner_means_no_team_writes(team):
    staging, remote, run = team
    r = run("team_remove.py", "list", HERA_OWNER="")
    assert r.returncode != 0 and "HERA_OWNER" in (r.stdout + r.stderr)


def test_git_never_prompts_for_credentials():
    sys.path.insert(0, str(REPO / "scripts"))
    import team_sync
    r = team_sync._run(["git", "-c", "credential.helper=", "ls-remote",
                        "https://example.invalid/none.git"])
    assert r.returncode != 0  # fails fast instead of waiting on a prompt
