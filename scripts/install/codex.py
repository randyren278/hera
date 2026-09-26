"""Codex user-level registration, sharing Hera's vault and skill sources."""
from __future__ import annotations

import os
import pathlib

import registration
import settings

BEGIN = "<!-- Hera managed: begin -->"
END = "<!-- Hera managed: end -->"


def home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex"))


def fragment(vault: pathlib.Path) -> dict:
    script = vault / "scripts" / "codex_hook.py"
    py = vault / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    def command(event: str) -> str:
        return f'"{py}" "{script}" {event}'
    return {"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": command("start")}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": command("prompt")}]}],
        "Stop": [{"hooks": [{"type": "command", "command": command("stop"), "async": True}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": command("end"), "timeout": 3}]}],
    }}


def _managed_block(vault: pathlib.Path) -> str:
    return (f"{BEGIN}\nHera vault guidance: read ~/.codex/hera/AGENTS.md when using Hera notes. "
            "Cite vault pages you use as (Source: [[Title]]).\n" f"{END}\n")


def _update_agents(path: pathlib.Path, vault: pathlib.Path, remove: bool = False) -> None:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    start = old.find(BEGIN)
    if start >= 0:
        end = old.find(END, start)
        if end >= 0:
            old = (old[:start].rstrip("\n") + "\n" + old[end + len(END):].lstrip("\n")).strip("\n")
    if not remove:
        old = (old + "\n\n" if old else "") + _managed_block(vault).rstrip("\n")
    if old:
        path.write_text(old.rstrip("\n") + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def install(vault: pathlib.Path, names: list[str], claude_locator: pathlib.Path) -> None:
    target = home()
    target.mkdir(parents=True, exist_ok=True)
    registration.register_skills(vault, target / "skills", names, target)
    link = target / "hera.env"
    if link.is_symlink():
        if link.resolve() != claude_locator.resolve():
            raise RuntimeError(f"refusing to replace foreign symlink: {link}")
    elif link.exists():
        if os.name != "nt" or not link.read_text(encoding="utf-8").startswith("# Hera vault locator"):
            raise RuntimeError(f"refusing to replace existing file: {link}")
        link.write_text(claude_locator.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        if os.name == "nt":
            link.write_text(claude_locator.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            link.symlink_to(claude_locator)
    hooks = target / "hooks.json"
    settings.merge_settings(hooks, fragment(vault))
    guidance = target / "hera" / "AGENTS.md"
    guidance.parent.mkdir(parents=True, exist_ok=True)
    if guidance.is_symlink():
        if guidance.resolve() != (vault / "AGENTS.md").resolve():
            raise RuntimeError(f"refusing to replace foreign symlink: {guidance}")
    elif guidance.exists():
        if os.name != "nt" or guidance.read_text(encoding="utf-8") != (vault / "AGENTS.md").read_text(encoding="utf-8"):
            raise RuntimeError(f"refusing to replace existing file: {guidance}")
    else:
        if os.name == "nt":
            guidance.write_text((vault / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8")
        else:
            guidance.symlink_to(vault / "AGENTS.md")
    _update_agents(target / "AGENTS.md", vault)


def uninstall(vault: pathlib.Path) -> None:
    target = home()
    registration.unregister_skills(vault, target / "skills", target)
    settings.strip_our_hooks(target / "hooks.json", fragment(vault))
    for path, source in ((target / "hera.env", pathlib.Path(os.environ.get("CLAUDE_HOME", pathlib.Path.home() / ".claude")) / "hera.env"),
                         (target / "hera" / "AGENTS.md", vault / "AGENTS.md")):
        if path.is_symlink() and path.resolve() == source.resolve():
            path.unlink()
        elif os.name == "nt" and path.is_file() and path.read_bytes() == source.read_bytes():
            path.unlink()
    _update_agents(target / "AGENTS.md", vault, remove=True)
