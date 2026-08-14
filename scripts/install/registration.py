"""registration.py — copy skills into ~/.claude/skills (no symlinks) + reverse.

A3: skills must be available in every directory, and Claude Code discovers
global skills from ``~/.claude/skills/``. We drop the symlink mirror and instead
**copy** each ``hera-*`` skill dir there (static markdown + assets, no live-edit
requirement). Copies need no admin/Developer-Mode on Windows and create no
symlink at all.

Uninstall reverses this **by content, not by readlink**: install records the
skill dirs it created in a manifest (``~/.claude/.hera-manifest``, JSON), and
uninstall removes exactly those. A skill dir that predates us (not in the
manifest, or a non-managed non-empty dir) is never clobbered or removed.
"""
from __future__ import annotations

import json
import pathlib
import shutil

MANIFEST_NAME = ".hera-manifest"


def _manifest_path(home: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(home) / MANIFEST_NAME


def _load_manifest(home: pathlib.Path) -> dict:
    p = _manifest_path(home)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _save_manifest(home: pathlib.Path, data: dict) -> None:
    p = _manifest_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def register_skills(vault: pathlib.Path, skills_dir: pathlib.Path,
                    skill_dirs: list[str], home: pathlib.Path) -> list[str]:
    """Copy each ``hera-*`` skill dir from the vault into ``skills_dir``.

    Refuses to overwrite a non-managed directory (one we didn't create per the
    manifest). Records created dirs in the manifest for clean uninstall.
    Returns the list of skill names registered. Idempotent: a dir we own is
    refreshed (removed + re-copied); a foreign dir raises RuntimeError.
    """
    vault = pathlib.Path(vault)
    skills_dir = pathlib.Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(home)
    owned = set(manifest.get("skills", []))

    registered: list[str] = []
    for name in skill_dirs:
        src = vault / ".claude" / "skills" / name
        if not src.is_dir():
            raise RuntimeError(f"register_skills: source skill missing: {src}")
        dst = skills_dir / name
        if dst.exists():
            if name not in owned and not dst.is_symlink():
                # A real, pre-existing dir we don't own — never clobber.
                raise RuntimeError(
                    f"register_skills: refusing to overwrite non-managed dir: {dst}")
            # Ours (or a stale symlink from a prior install) — refresh cleanly.
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
        shutil.copytree(src, dst)
        owned.add(name)
        registered.append(name)

    manifest["skills"] = sorted(owned)
    _save_manifest(home, manifest)
    return registered


def unregister_skills(vault: pathlib.Path, skills_dir: pathlib.Path,
                      home: pathlib.Path) -> int:
    """Remove the skill dirs we created (per manifest) from ``skills_dir``.

    Returns the count removed. Only touches manifest-tracked dirs; leaves
    foreign dirs untouched. Clears the manifest's skill list afterward.
    """
    skills_dir = pathlib.Path(skills_dir)
    manifest = _load_manifest(home)
    owned = list(manifest.get("skills", []))
    removed = 0
    for name in owned:
        dst = skills_dir / name
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
            removed += 1
        elif dst.is_dir():
            shutil.rmtree(dst)
            removed += 1
    manifest["skills"] = []
    _save_manifest(home, manifest)
    # If the manifest now holds nothing meaningful, remove it.
    if not any(manifest.get(k) for k in manifest):
        mp = _manifest_path(home)
        if mp.exists():
            mp.unlink()
    return removed
