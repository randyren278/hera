"""settings.py — generate + merge/strip the Hera hook fragment.

Pure-Python port of lib.sh's settings helpers, plus install-time generation of
the hook fragment (replacing the static settings_fragment.json). The fragment's
command strings come from hookcmd.hook_command(os_name, ...), so they're correct
for the OS install.py runs on.

All writes are atomic (tmp + os.replace). Dedup key for merge/strip is the tuple
of command strings inside a hook-group's "hooks" array — same key lib.sh used.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time

import hookcmd

# Per-event metadata carried from the original settings_fragment.json.
_EVENT_META = {
    "SessionStart": {},
    "UserPromptSubmit": {"timeout": 10},
    "Stop": {"async": True, "timeout": 120},
    "SessionEnd": {"async": True, "timeout": 600},
}


def build_fragment(vault: pathlib.Path, os_name: str | None = None) -> dict:
    """Build the hooks fragment dict for the given OS (default: this host)."""
    os_name = os_name or os.name
    hooks: dict[str, list] = {}
    for event, meta in _EVENT_META.items():
        entry = {"type": "command", "command": hookcmd.hook_command(os_name, vault, event)}
        entry.update(meta)
        hooks[event] = [{"hooks": [entry]}]
    return {"hooks": hooks}


class SettingsError(ValueError):
    """A settings file Hera must edit is unreadable; nothing was changed."""


def load_json(path: pathlib.Path) -> dict:
    """Parse a JSON settings file, or raise SettingsError naming the file."""
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SettingsError(f"{path} is not valid JSON ({e}). Fix it and re-run; "
                            "nothing was changed.") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object. Fix it and re-run; "
                            "nothing was changed.")
    return data


# --- signatures / merge / strip -------------------------------------------

def _sig(entry: dict) -> tuple:
    return tuple(h.get("command") for h in entry.get("hooks", []))


def settings_has_our_hooks(target: pathlib.Path, fragment: dict) -> bool:
    """True iff ``target`` exists and already contains every hook-group in
    ``fragment`` (by command-tuple signature)."""
    target = pathlib.Path(target)
    if not target.exists():
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    target_hooks = data.get("hooks", {})
    for event, groups in fragment.get("hooks", {}).items():
        existing_sigs = {_sig(e) for e in target_hooks.get(event, [])}
        for g in groups:
            if _sig(g) not in existing_sigs:
                return False
    return True


def merge_settings(target: pathlib.Path, fragment: dict) -> None:
    """JSON-merge ``fragment`` into ``target``, preserving pre-existing keys.
    Idempotent: dedupes hook-groups by command-tuple signature."""
    target = pathlib.Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = load_json(target) if target.exists() else {}
    data.setdefault("hooks", {})
    for event, groups in fragment.get("hooks", {}).items():
        existing = data["hooks"].setdefault(event, [])
        existing_sigs = {_sig(e) for e in existing}
        for g in groups:
            if _sig(g) not in existing_sigs:
                existing.append(g)
                existing_sigs.add(_sig(g))
    _atomic_json(target, data)


_HERA_SCRIPTS = tuple(f"/.claude/hooks/{s}" for s in hookcmd.HOOK_SCRIPTS.values()) + (
    "/scripts/codex_hook.py",)
_QUOTED = re.compile(r'^"(?P<py>[^"]+)" "(?P<script>[^"]+)"(?: \w+)?$')
_VENV_PY = ("/.venv/bin/python", "/.venv/Scripts/python.exe")
LEGACY = "<legacy ~/.claude/hooks install>"


def hera_hook_vault(command: str) -> str | None:
    """The vault a Hera hook command runs from, or None if it isn't one.

    Recognises the current POSIX/Windows command form, the Codex hook form, and
    the legacy ``. ~/.claude/hera.env && … ~/.claude/hooks/…`` form (returned
    as ``LEGACY`` because it carries no vault path).

    A script name alone is not proof — a user's own ~/.claude/hooks/
    session_start.py is common. A Hera hook runs under ITS OWN vault's .venv
    interpreter, so the interpreter path must be that same vault's."""
    if "/.claude/hera.env" in command and "/.claude/hooks/" in command:
        return LEGACY
    m = _QUOTED.match(command.strip())
    if not m:
        return None
    script = m.group("script").replace("\\", "/")
    py = m.group("py").replace("\\", "/")
    for suffix in _HERA_SCRIPTS:
        if script.endswith(suffix):
            vault = script[: -len(suffix)]
            if not any(_norm_vault(py) == _norm_vault(vault + v) for v in _VENV_PY):
                return None
            # An existing directory must actually be a Hera vault (a user may
            # run their own hook from ~/.venv). A path that no longer exists is
            # a moved/deleted vault whose dead hooks are safe to clear.
            root = pathlib.Path(vault)
            if root.exists() and not (root / "scripts" / "hera_db.py").exists():
                return None
            return vault
    return None


def _norm_vault(p) -> str:
    return os.path.normcase(str(p).replace("\\", "/").rstrip("/"))


def hera_vaults_in_settings(target: pathlib.Path) -> list[str]:
    """Sorted distinct vaults whose Hera hooks ``target`` registers."""
    try:
        data = json.loads(pathlib.Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = {hera_hook_vault(h.get("command", ""))
             for groups in data.get("hooks", {}).values()
             for g in groups for h in g.get("hooks", [])}
    found.discard(None)
    return sorted(found)


def remove_other_vault_hooks(target: pathlib.Path, vault: pathlib.Path) -> list[str]:
    """Drop Hera hook entries that point at any vault other than ``vault``.

    Only entries recognised by ``hera_hook_vault`` are touched; a group left
    empty is dropped. Returns the sorted distinct vaults removed."""
    target = pathlib.Path(target)
    if not target.exists():
        return []
    data = load_json(target)
    removed: set[str] = set()
    hooks = data.get("hooks", {})
    for ev, groups in list(hooks.items()):
        kept_groups = []
        for g in groups:
            entries = []
            for h in g.get("hooks", []):
                owner = hera_hook_vault(h.get("command", ""))
                if owner is not None and _norm_vault(owner) != _norm_vault(vault):
                    removed.add(owner)
                else:
                    entries.append(h)
            if entries:
                kept_groups.append({**g, "hooks": entries})
        if kept_groups:
            hooks[ev] = kept_groups
        else:
            del hooks[ev]
    if removed:
        _atomic_json(target, data)
    return sorted(removed)


def strip_our_hooks(target: pathlib.Path, fragment: dict) -> str:
    """Remove every hook-group matching ``fragment`` from ``target``. Deletes the
    file if our hooks were its sole content. Returns a status string; no-op if
    ``target`` is absent."""
    target = pathlib.Path(target)
    if not target.exists():
        return "no settings.json"
    data = load_json(target)
    frag_sigs = {ev: {_sig(g) for g in groups}
                 for ev, groups in fragment.get("hooks", {}).items()}
    hooks = data.get("hooks", {})
    changed = False
    for ev, groups in list(hooks.items()):
        kept = [g for g in groups if _sig(g) not in frag_sigs.get(ev, set())]
        if kept != groups:
            changed = True
            if kept:
                hooks[ev] = kept
            else:
                del hooks[ev]
    if not changed:
        return "no Hera hook entries to remove"
    other_keys = [k for k in data.keys() if k != "hooks"]
    if not hooks and not other_keys:
        target.unlink()
        return "removed settings.json (contained only Hera hooks)"
    if not hooks:
        del data["hooks"]  # leave the file as it was before install
    _atomic_json(target, data)
    return "stripped Hera hook entries from settings.json"


# --- backup ------------------------------------------------------

def backup_file(src: pathlib.Path) -> pathlib.Path | None:
    """Copy ``src`` to ``src.hera-backup.<timestamp>``. Returns the backup path,
    or None if ``src`` doesn't exist."""
    src = pathlib.Path(src)
    if not src.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(src.name + f".hera-backup.{ts}")
    n = 0
    while dst.exists():
        n += 1
        dst = src.with_name(src.name + f".hera-backup.{ts}.{n}")
    dst.write_bytes(src.read_bytes())
    return dst


def _atomic_json(path: pathlib.Path, data: dict) -> None:
    # Write through a symlink (dotfiles managers keep settings.json as one);
    # os.replace on the link itself would swap it for a plain file.
    path = pathlib.Path(os.path.realpath(path))
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
