#!/usr/bin/env python3
"""hera_cli.py — one OS-neutral entry point for every skill engine call.

Skills used to invoke engines with a hardcoded ``"$VAULT/.venv/bin/python"``
(POSIX-only path) after a bash-only preamble that sourced the locator and
guarded ``${HERA_VAULT:?}``. None of that runs on native Windows.

This dispatcher removes all of it. A skill runs exactly one shape on any OS:

    python "<VAULT>/scripts/hera_cli.py" <engine> [args...]

where ``<VAULT>`` is the absolute vault path (the agent substitutes the value
of ``$HERA_VAULT``). ``hera_cli`` then:

  1. self-locates the vault from ``$HERA_VAULT`` or its own ``__file__``,
  2. resolves the per-OS venv interpreter (``Scripts/python.exe`` vs
     ``bin/python``), and
  3. re-execs the requested engine under that interpreter.

So the bare ``python`` on PATH only needs to reach this launcher; the heavy
engines still run under the vault's venv. No ``.``-source, no ``${VAR:?}``, no
``bash``, no ``.venv/bin/python`` literal in any SKILL.md.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

# Engines a skill may invoke, mapped to their script under scripts/.
ENGINES = {
    "ingest": "ingest.py",
    "seed_index": "seed_index.py",
    "publish": "publish.py",
    "conflicts": "conflicts.py",
    "prune": "prune.py",
    "team_sync": "team_sync.py",
    "team_search": "team_search.py",
    "team_remove": "team_remove.py",
    "hera_db": "hera_db.py",
    "preflight": "install/preflight.py",
    "search": "search.py",
    "embed": "embed.py",
}


def _vault() -> pathlib.Path:
    env = os.environ.get("HERA_VAULT")
    if env:
        return pathlib.Path(env).resolve()
    # scripts/hera_cli.py → parent is scripts/, its parent is the vault.
    return pathlib.Path(__file__).resolve().parent.parent


def _venv_python(vault: pathlib.Path) -> pathlib.Path:
    if os.name == "nt":
        return vault / ".venv" / "Scripts" / "python.exe"
    return vault / ".venv" / "bin" / "python"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python <VAULT>/scripts/hera_cli.py <engine> [args...]")
        print("engines: " + ", ".join(sorted(ENGINES)))
        return 0 if argv else 2

    engine, rest = argv[0], argv[1:]
    if engine not in ENGINES:
        sys.stderr.write(f"hera_cli: unknown engine {engine!r}; "
                         f"known: {', '.join(sorted(ENGINES))}\n")
        return 2

    vault = _vault()
    py = _venv_python(vault)
    interp = str(py) if py.exists() else sys.executable
    script = vault / "scripts" / ENGINES[engine]
    if not script.exists():
        sys.stderr.write(f"hera_cli: engine script missing: {script}\n")
        return 2

    # Run under the resolved venv interpreter so engine imports (sqlite-vec,
    # ulid, requests) resolve regardless of which python launched hera_cli.
    proc = subprocess.run([interp, str(script), *rest])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
