"""CP-1: venv.py resolves the interpreter path per-OS and bootstraps a venv."""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import venv as venv_mod  # noqa: E402


def test_windows_interpreter_path(tmp_path):
    py = venv_mod.venv_python(tmp_path, os_name="nt")
    assert py == tmp_path / ".venv" / "Scripts" / "python.exe"


def test_posix_interpreter_path(tmp_path):
    py = venv_mod.venv_python(tmp_path, os_name="posix")
    assert py == tmp_path / ".venv" / "bin" / "python"


def test_default_os_matches_host(tmp_path):
    import os
    py = venv_mod.venv_python(tmp_path)
    if os.name == "nt":
        assert py.name == "python.exe"
        assert py.parent.name == "Scripts"
    else:
        assert py.name == "python"
        assert py.parent.name == "bin"


def test_ensure_venv_bootstraps(tmp_path):
    """Create a real venv (no pip deps, to keep the test fast) and find its python."""
    py = venv_mod.ensure_venv(tmp_path, install_deps=False)
    assert py.exists(), f"expected interpreter at {py}"
    assert py == venv_mod.venv_python(tmp_path)
    # Idempotent: second call returns the same existing interpreter.
    assert venv_mod.ensure_venv(tmp_path, install_deps=False) == py
