"""Run the hermetic shell checks under pytest so one command covers them all.

Every script listed here works in a mktemp scratch vault or only reads the
source tree; none touches the live vault or the real ~/.claude.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

TESTS = pathlib.Path(__file__).resolve().parent
SCRIPTS = [
    "check_gitignore_blocks_junk.sh",
    "check_inject_paths_resolve.sh",
    "check_retrieve_sync_baseline.sh",
    "test_install_lib.sh",
    "test_team_remove.sh",
]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("script", SCRIPTS)
def test_shell_check(script):
    r = subprocess.run(["bash", str(TESTS / script)], capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, f"{script} failed:\n{r.stdout}\n{r.stderr}"
