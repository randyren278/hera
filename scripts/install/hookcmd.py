"""hookcmd.py — generate each hook's settings.json command string, per-OS.

The command string is what Claude Code hands to the host shell for a hook.
Because we drop the ``~/.claude/hooks`` symlink mirror (A3), hooks run
**in-repo** and every hook script resolves its REPO from ``$HERA_VAULT``
OR its own ``__file__`` — with the in-repo path, ``__file__`` is already correct,
so the command needs no env sourcing at all.

That lets the command be a single interpreter+script invocation on both OSes,
with absolute paths and no shell operators:

  POSIX:   "<vault>/.venv/bin/python" "<vault>/.claude/hooks/<hook>"
  Windows: "<vault>\\.venv\\Scripts\\python.exe" "<vault>\\.claude\\hooks\\<hook>"

Neither form uses ``.``-source, ``&&``, or ``$VAR`` — the Windows requirement.
"""
from __future__ import annotations

import os
import pathlib

HOOK_SCRIPTS = {
    "SessionStart": "session_start.py",
    "UserPromptSubmit": "prompt_inject.py",
    "Stop": "stop_score.py",
    "SessionEnd": "session_end_file.py",
}


def _q(path: str) -> str:
    """Double-quote a path for the target shell. Both cmd.exe and POSIX sh
    treat a double-quoted token as a single argument; our paths contain no
    embedded double-quotes, so simple wrapping is sufficient and safe."""
    return f'"{path}"'


def hook_command(os_name: str, vault: pathlib.Path, hook_event: str) -> str:
    """Return the command string for ``hook_event`` on ``os_name`` ('nt'|'posix')."""
    script = HOOK_SCRIPTS[hook_event]
    vault = pathlib.Path(vault)
    if os_name == "nt":
        # Build Windows-style backslash paths deterministically regardless of
        # the host we generate on (so an os.name-forced sim produces real
        # Windows strings on a POSIX CI host).
        base = str(vault).replace("/", "\\").rstrip("\\")
        py = f"{base}\\.venv\\Scripts\\python.exe"
        hook = f"{base}\\.claude\\hooks\\{script}"
    else:
        base = vault.as_posix()
        py = f"{base}/.venv/bin/python"
        hook = f"{base}/.claude/hooks/{script}"
    return f"{_q(py)} {_q(hook)}"


def has_posixisms(command: str) -> bool:
    """True if a command string contains a construct cmd.exe can't run:
    a leading ``.``-source, an ``&&`` chain, or a ``$VAR`` expansion."""
    if "&&" in command:
        return True
    if "$" in command:
        return True
    # A ``.``-source is a line that begins with ``. `` (dot + space).
    if command.lstrip().startswith(". "):
        return True
    return False
