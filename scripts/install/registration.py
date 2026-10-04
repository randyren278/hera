"""Register Hera skills from one vault source in either agent's skills directory.

A3: skills must be available in every directory. POSIX registrations point at
the vault's canonical ``SKILL.md`` source; Windows uses copies because symlink
creation can require extra privileges.

Uninstall reverses this by ownership: install records the
skill dirs it created in a manifest (one per client), and
uninstall removes exactly those. A skill dir that predates us (not in the
manifest, or a non-managed non-empty dir) is never clobbered or removed.
"""
from __future__ import annotations

import json
import os
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


def get_flag(home: pathlib.Path, key: str):
    return _load_manifest(home).get(key)


def set_flag(home: pathlib.Path, key: str, value) -> None:
    """Record (or, with a falsy value, clear) an install fact in the manifest.
    The manifest file is removed once it records nothing."""
    manifest = _load_manifest(home)
    if value:
        manifest[key] = value
    else:
        manifest.pop(key, None)
    if any(manifest.get(k) for k in manifest):
        _save_manifest(home, manifest)
    elif _manifest_path(home).exists():
        _manifest_path(home).unlink()


def register_skills(vault: pathlib.Path, skills_dir: pathlib.Path,
                    skill_dirs: list[str], home: pathlib.Path,
                    os_name: str | None = None) -> list[str]:
    """Link skills on POSIX; copy on Windows where symlinks need privileges.

    Refuses to overwrite a non-managed directory (one we didn't create per the
    manifest). Records created dirs in the manifest for clean uninstall.
    Returns the list of skill names registered. Idempotent: a dir we own is
    refreshed; a foreign dir raises RuntimeError.
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
        if dst.exists() or dst.is_symlink():
            if name not in owned:
                # A real, pre-existing dir we don't own — never clobber.
                raise RuntimeError(
                    f"register_skills: refusing to overwrite non-managed dir: {dst}")
            # Ours — refresh cleanly.
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
        if (os_name or os.name) == "nt":
            shutil.copytree(src, dst)
        else:
            dst.symlink_to(src, target_is_directory=True)
        owned.add(name)
        registered.append(name)

    manifest["skills"] = sorted(owned)
    manifest["vault"] = str(vault.resolve())
    _save_manifest(home, manifest)
    return registered


def unregister_skills(vault: pathlib.Path, skills_dir: pathlib.Path,
                      home: pathlib.Path) -> int:
    """Remove the skill dirs we created (per manifest) from ``skills_dir``.

    Returns the count removed. Only touches manifest-tracked dirs that belong
    to ``vault`` — a link into another vault (or copies registered by another
    vault, per the manifest) is left alone and stays tracked. Foreign dirs are
    never touched.
    """
    skills_dir = pathlib.Path(skills_dir)
    vault = pathlib.Path(vault).resolve()
    manifest = _load_manifest(home)
    owned = list(manifest.get("skills", []))
    copies_ours = manifest.get("vault") in (None, str(vault))
    removed, kept = 0, []
    for name in owned:
        dst = skills_dir / name
        if dst.is_symlink():
            try:
                pathlib.Path(os.path.realpath(dst)).relative_to(vault)
            except ValueError:
                kept.append(name)  # another vault's registration
                continue
            dst.unlink()
            removed += 1
        elif not copies_ours:
            kept.append(name)
        elif dst.is_file():
            dst.unlink()
            removed += 1
        elif dst.is_dir():
            shutil.rmtree(dst)
            removed += 1
    manifest["skills"] = kept
    _save_manifest(home, manifest)
    # If the manifest now holds nothing meaningful, remove it.
    if not manifest["skills"]:
        manifest.pop("vault", None)
    if not any(manifest.get(k) for k in manifest):
        mp = _manifest_path(home)
        if mp.exists():
            mp.unlink()
    return removed
