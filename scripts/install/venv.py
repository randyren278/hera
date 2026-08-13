"""venv.py — resolve and bootstrap the vault's .venv, per-OS.

Pure-Python port of preflight.sh's venv-bootstrap responsibility. The
interpreter path differs by OS: ``.venv\\Scripts\\python.exe`` on Windows
(``os.name == 'nt'``), ``.venv/bin/python`` elsewhere. We never hardcode a
Homebrew path — bootstrap candidates come from ``sys.executable`` and
``shutil.which`` so it works on any host.

The venv is only useful if its interpreter has ``sqlite3`` extension loading
(needed by sqlite-vec); we prefer a bootstrap interpreter that has it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import pathlib

PIP_DEPS = ["sqlite-vec", "ulid-py", "requests", "pyyaml", "pytest"]


def venv_python(vault: pathlib.Path, os_name: str | None = None) -> pathlib.Path:
    """Return the venv interpreter path for the given OS (default: this host)."""
    os_name = os_name or os.name
    venv = pathlib.Path(vault) / ".venv"
    if os_name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _has_extension_loading(python: str) -> bool:
    try:
        r = subprocess.run(
            [python, "-c",
             "import sqlite3,sys;"
             "sys.exit(0 if hasattr(sqlite3.Connection,'enable_load_extension') else 1)"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _bootstrap_candidates() -> list[str]:
    """Interpreters to try when creating the venv, best-first.

    Start with the interpreter running this script, then any python3/python on
    PATH. No hardcoded platform paths.
    """
    cands: list[str] = []
    if sys.executable:
        cands.append(sys.executable)
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    # De-dup, preserve order.
    seen: set[str] = set()
    uniq: list[str] = []
    for c in cands:
        rp = os.path.realpath(c)
        if rp not in seen:
            seen.add(rp)
            uniq.append(c)
    return uniq


def ensure_venv(vault: pathlib.Path, install_deps: bool = True) -> pathlib.Path:
    """Ensure ``vault/.venv`` exists with pip deps; return the interpreter path.

    Idempotent. The venv is *created* only when its interpreter is absent, but
    the pip deps are *reconciled* on every call (when ``install_deps``): pip
    no-ops on an already-satisfied venv and installs the missing packages on a
    stale one. Reconciling unconditionally is what repairs a pre-existing
    ``.venv`` that predates a dep being added to ``PIP_DEPS`` — otherwise the
    early return on ``py.exists()`` would skip the install and leave e.g.
    ``sqlite-vec``/``ulid-py`` missing. Raises RuntimeError if no suitable
    bootstrap interpreter is found or creation/install fails.
    """
    vault = pathlib.Path(vault)
    py = venv_python(vault)
    # Rebuild the venv when it's absent OR when its interpreter can't load
    # sqlite extensions. The latter guards the case where a prior run built the
    # venv from an incapable python (e.g. python.org's macOS framework build):
    # the vault would import fine but sqlite-vec would fail to load at runtime.
    needs_build = (not py.exists()) or (not _has_extension_loading(str(py)))
    if needs_build:
        if py.exists() and not _has_extension_loading(str(py)):
            import shutil as _shutil
            _shutil.rmtree(vault / ".venv", ignore_errors=True)
        boot = ""
        fallback = ""
        for cand in _bootstrap_candidates():
            if _has_extension_loading(cand):
                boot = cand
                break
            fallback = fallback or cand
        boot = boot or fallback
        if not boot:
            raise RuntimeError("venv: no python3 interpreter found to bootstrap .venv")
        if not _has_extension_loading(boot):
            raise RuntimeError(
                "venv: no python3 with sqlite3 extension loading found to "
                "bootstrap .venv (sqlite-vec requires it). Install one, e.g. "
                "`brew install python`, and re-run."
            )

        r = subprocess.run([boot, "-m", "venv", str(vault / ".venv")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"venv: creation failed: {r.stderr.strip()}")

    if install_deps:
        r = subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", *PIP_DEPS],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"venv: pip install failed: {r.stderr.strip()}")
    return py


if __name__ == "__main__":
    vault = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2]
    print(ensure_venv(vault))
