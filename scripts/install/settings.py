"""settings.py — generate + merge/strip the Second Brain hook fragment.

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
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
    else:
        data = {}
    data.setdefault("hooks", {})
    for event, groups in fragment.get("hooks", {}).items():
        existing = data["hooks"].setdefault(event, [])
        existing_sigs = {_sig(e) for e in existing}
        for g in groups:
            if _sig(g) not in existing_sigs:
                existing.append(g)
                existing_sigs.add(_sig(g))
    _atomic_json(target, data)


def strip_our_hooks(target: pathlib.Path, fragment: dict) -> str:
    """Remove every hook-group matching ``fragment`` from ``target``. Deletes the
    file if our hooks were its sole content. Returns a status string; no-op if
    ``target`` is absent."""
    target = pathlib.Path(target)
    if not target.exists():
        return "no settings.json"
    data = json.loads(target.read_text(encoding="utf-8"))
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
        return "no second-brain hook entries to remove"
    other_keys = [k for k in data.keys() if k != "hooks"]
    if not hooks and not other_keys:
        target.unlink()
        return "removed settings.json (contained only second-brain hooks)"
    _atomic_json(target, data)
    return "stripped second-brain hook entries from settings.json"


# --- backup / restore ------------------------------------------------------

def backup_file(src: pathlib.Path) -> pathlib.Path | None:
    """Copy ``src`` to ``src.brain-backup.<timestamp>``. Returns the backup path,
    or None if ``src`` doesn't exist."""
    src = pathlib.Path(src)
    if not src.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(src.name + f".brain-backup.{ts}")
    n = 0
    while dst.exists():
        n += 1
        dst = src.with_name(src.name + f".brain-backup.{ts}.{n}")
    dst.write_bytes(src.read_bytes())
    return dst


def restore_latest_backup(target: pathlib.Path) -> bool:
    """Restore the most-recent ``target.brain-backup.*`` to ``target``.
    Returns False if no backup exists."""
    target = pathlib.Path(target)
    backups = sorted(
        target.parent.glob(target.name + ".brain-backup.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        return False
    target.write_bytes(backups[0].read_bytes())
    return True


def _atomic_json(path: pathlib.Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
