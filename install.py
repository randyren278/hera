#!/usr/bin/env python3
"""install.py — cross-platform installer for the Hera vault.

Pure-Python replacement for install.sh/lib.sh/locator.sh/preflight.sh's
orchestration. Works on POSIX and native Windows (no bash, no symlinks).

  python install.py                    install (idempotent; safe to re-run)
  python install.py --dry-run          print every action without executing
  python install.py --uninstall        remove global hooks/skills, restore backup
  python install.py --with-global-claudemd
                                       append vault CLAUDE.md into ~/.claude/CLAUDE.md
  python install.py --install-ollama   install/start Ollama without prompting
  python install.py --no-install-ollama  skip Ollama provisioning (guide only)
  python install.py --help             this message

Self-locates the vault via ``Path(__file__).resolve().parent``. The heavy
lifting lives in ``scripts/install/*`` (venv, locator, hookcmd, settings,
preflight); this file orchestrates the ordered steps and owns the CLI.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

VAULT = pathlib.Path(__file__).resolve().parent
INSTALL_PKG = VAULT / "scripts" / "install"
sys.path.insert(0, str(INSTALL_PKG))

import ui  # scripts/install/ui.py — TTY-aware pretty-print layer


# --------------------------------------------------------------------------
# interpreter self-selection (runs before any step)
# --------------------------------------------------------------------------
# The vault needs a python whose sqlite3 supports loadable extensions (for
# sqlite-vec). Not all python3 builds do — notably the python.org macOS
# framework build ships sqlite3 with extension loading compiled out. If the
# user launched install.py under such a python, we find a capable interpreter
# on the machine and re-exec install.py under it, so the "wrong interpreter"
# preflight failure can never reach the user.

_REEXEC_GUARD = "HERA_REEXEC"


def _this_python_can_load_extensions() -> bool:
    return hasattr(sqlite3.Connection, "enable_load_extension")


def _find_capable_python() -> str | None:
    """Return the path to a python3 on this machine whose sqlite3 supports
    loadable extensions, or None if none is found. Never returns the current
    interpreter (we already know it's incapable when this is called)."""
    here = os.path.realpath(sys.executable) if sys.executable else ""
    seen: set[str] = set()
    for name in ("python3", "python", "python3.13", "python3.12", "python3.11"):
        p = shutil.which(name)
        if not p:
            continue
        rp = os.path.realpath(p)
        if rp == here or rp in seen:
            continue
        seen.add(rp)
        try:
            r = subprocess.run(
                [p, "-c",
                 "import sqlite3,sys;"
                 "sys.exit(0 if hasattr(sqlite3.Connection,'enable_load_extension') else 1)"],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0:
                return p
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _ensure_capable_interpreter(argv: list[str]) -> None:
    """If the launching python can't load sqlite extensions, re-exec under one
    that can. If none exists, fail early with the exact remedy. No-op when the
    current interpreter is already capable, or if we've already re-exec'd once
    (guarded by an env var to prevent an infinite loop)."""
    if _this_python_can_load_extensions():
        return
    if os.environ.get(_REEXEC_GUARD):
        # We already relaunched once and still landed on an incapable python.
        # Don't loop — let preflight report it plainly.
        return
    capable = _find_capable_python()
    if capable:
        print(f"install: this python ({sys.executable}) can't load sqlite "
              f"extensions; relaunching under {capable}", file=sys.stderr)
        env = dict(os.environ, **{_REEXEC_GUARD: "1"})
        os.execve(capable, [capable, str(pathlib.Path(__file__).resolve()), *argv], env)
    else:
        print(
            "install: the python you ran this with lacks sqlite3 extension "
            "loading, and no capable python3 was found on PATH.\n"
            f"    ran with: {sys.executable}\n"
            "    Install a python whose sqlite3 supports loadable extensions\n"
            "    (e.g. Homebrew: `brew install python`) and re-run:\n"
            "        /opt/homebrew/bin/python3 install.py\n"
            "    or point install.py at any capable interpreter you have.",
            file=sys.stderr,
        )
        sys.exit(1)

HOOK_FILES = ["session_start.py", "prompt_inject.py", "stop_score.py", "session_end_file.py"]
SKILL_DIRS = ["hera-setup", "hera-ingest", "hera-conflicts", "hera-prune", "hera-team"]

HERA_MD_BEGIN = "# >>> Hera (managed by install.py) >>>"
HERA_MD_END = "# <<< Hera <<<"


def claude_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CLAUDE_HOME", pathlib.Path.home() / ".claude"))


class Runner:
    """Prints actions; executes them unless dry-run."""

    def __init__(self, dry: bool):
        self.dry = dry

    def do(self, desc: str, fn=None):
        prefix = "[dry] " if self.dry else ""
        ui.info(prefix + desc)
        if self.dry or fn is None:
            return None
        return fn()


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def do_install(dry: bool, with_global_md: bool, ollama_yes: bool | None = None) -> int:
    home = claude_home()
    loc_env = home / "hera.env"
    global_settings = home / "settings.json"
    global_skills = home / "skills"
    project_settings = VAULT / ".claude" / "settings.json"
    project_disabled = project_settings.with_suffix(".json.disabled")
    global_md = home / "CLAUDE.md"
    vault_md = VAULT / "CLAUDE.md"
    r = Runner(dry)

    ui.header("Hera - installer", {
        "vault": str(VAULT),
        "mode ": f"install   dry={int(dry)}",
        "home ": str(home),
    })

    # Step 1: preflight (bootstraps .venv, checks Ollama).
    ui.step("step 1/7: preflight")
    if dry:
        ui.info("[dry] ensure .venv + reconcile pip deps")
        import ollama_provision  # scripts/install/ollama_provision.py
        ollama_provision.ensure_ollama(dry=True, assume_yes=ollama_yes)
        import ollama  # scripts/install/ollama.py — local module shadows any pip 'ollama'
        # Route ollama's dry-run emission through ui (ollama.py is out of scope to
        # edit). Call the same pure predicates ollama.ensure_model(dry=True) calls,
        # in the same order, and emit the byte-identical wording via ui.info so its
        # em-dash degrades to '--' on the non-TTY path.
        if not ollama._daemon_up():
            ui.info(f"[dry] ollama daemon down — would pull {ollama.DEFAULT_MODEL!r} once it's up")
        elif ollama._model_present(ollama.DEFAULT_MODEL):
            ui.info(f"[dry] ollama model {ollama.DEFAULT_MODEL!r} already present — nothing to pull")
        else:
            ui.info(f"[dry] would pull ollama model {ollama.DEFAULT_MODEL!r}")
        ui.info("[dry] run scripts/install/preflight.py (checks env)")
    else:
        import venv as venv_mod  # scripts/install/venv.py
        venv_py = venv_mod.ensure_venv(VAULT)
        # Provision Ollama (install binary + start daemon) before the model pull.
        # Best-effort: a note on failure, never fatal — preflight below is the gate.
        import ollama_provision  # scripts/install/ollama_provision.py

        def _prompt_install_ollama() -> bool:
            if not sys.stdin.isatty():
                return True  # non-TTY (e.g. run via Claude) → proceed
            try:
                reply = input("Ollama not found — install it now? [Y/n] ").strip().lower()
            except EOFError:
                return True
            return reply in ("", "y", "yes")

        ok, detail = ollama_provision.ensure_ollama(
            dry=False, assume_yes=ollama_yes, prompt_fn=_prompt_install_ollama)
        if not ok:
            ui.warn(f"ollama: {detail}")
        # Provision the embedding model before preflight verifies it. Best-effort:
        # a note on failure, never fatal — preflight below is the pass/fail gate.
        import ollama  # scripts/install/ollama.py
        ok, detail = ollama.ensure_model(dry=False)
        if not ok:
            ui.info(f"ollama: {detail}")
        preflight_py = VAULT / "scripts" / "install" / "preflight.py"
        if preflight_py.exists():
            # Run preflight UNDER THE VENV INTERPRETER, not this process. The
            # library-import and sqlite-extension checks must reflect the python
            # the vault actually runs on (the venv), not whatever launched
            # install.py — those can differ (see interpreter self-selection).
            rc = subprocess.run([str(venv_py), str(preflight_py)])
            if rc.returncode != 0:
                print("install: preflight failed — fix the reported issues and re-run.",
                      file=sys.stderr)
                return 1
        else:
            ui.info("(preflight.py not present yet — venv bootstrapped)")

    # Step 2: initialize hera.db (idempotent).
    ui.step("step 2/7: hera.db")
    if dry:
        ui.info("[dry] hera_db.py --init (if hera.db absent)")
    else:
        db = VAULT / "hera.db"
        if db.exists() and db.stat().st_size > 0:
            ui.info("hera.db already present — skipping --init")
        else:
            import venv as venv_mod
            py = venv_mod.venv_python(VAULT)
            rc = subprocess.run([str(py), str(VAULT / "scripts" / "hera_db.py"), "--init"],
                                capture_output=True, text=True)
            if rc.returncode != 0:
                print(f"install: hera.db init failed: {rc.stderr}", file=sys.stderr)
                return 1
            ui.info("hera.db initialized")

    # Step 3: locator file.
    ui.step(f"step 3/7: locator ({loc_env})")
    if dry:
        ui.info(f"[dry] write HERA_VAULT into {loc_env}")
    else:
        import locator
        locator.write_locator(VAULT, loc_env)

    # Step 4: merge generated hook fragment into global settings.json.
    ui.step(f"step 4/7: merge into {global_settings}")
    if dry:
        ui.info("[dry] backup settings.json (unless already installed), generate + merge hooks")
    else:
        import settings as settings_mod  # scripts/install/settings.py (Phase 2)
        home.mkdir(parents=True, exist_ok=True)
        fragment = settings_mod.build_fragment(VAULT, os.name)
        if settings_mod.settings_has_our_hooks(global_settings, fragment):
            ui.info("already installed — preserving existing backup (no re-backup)")
        else:
            backup = settings_mod.backup_file(global_settings)
            if backup:
                ui.info(f"backed up existing settings.json → {backup}")
        settings_mod.merge_settings(global_settings, fragment)
        ui.info("merged.")

    # Step 5: register skills by COPYING into ~/.claude/skills/ (no symlinks).
    ui.step(f"step 5/7: copy skills into {global_skills}")
    if dry:
        for s in SKILL_DIRS:
            ui.info(f"[dry] copy .claude/skills/{s} → {global_skills / s}")
    else:
        import registration
        registration.register_skills(VAULT, global_skills, SKILL_DIRS, home)

    # Step 6: disable project-local settings.json so hooks don't double-fire.
    ui.step("step 6/7: disable project-local settings.json")
    if project_settings.exists():
        r.do(f"move {project_settings} → {project_disabled}",
             lambda: os.replace(project_settings, project_disabled))
    else:
        ui.info("(no project-local settings.json to disable)")

    # Step 7: optional global CLAUDE.md block.
    ui.step("step 7/7: global CLAUDE.md (optional)")
    if dry:
        ui.info(f"[dry] append vault CLAUDE.md into {global_md} (only if opted in)")
    else:
        should = with_global_md or (sys.stdin.isatty() and _prompt_global_md(global_md))
        if should:
            home.mkdir(parents=True, exist_ok=True)
            _append_global_claudemd(global_md, vault_md)
            ui.info(f"appended Hera block to {global_md}")
        else:
            ui.info("(skipped — global CLAUDE.md unchanged)")

    print()
    ui.plain("install: complete.")
    ui.info("Restart Claude Code, then run /hera-setup to finish vault scaffolding.")
    ui.info(f"To undo: python {VAULT / 'install.py'} --uninstall")
    return 0


def _prompt_global_md(global_md: pathlib.Path) -> bool:
    try:
        print(f"  Append the vault CLAUDE.md into {global_md}? [y/N] ", end="")
        reply = input().strip().lower()
        return reply in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------

def do_uninstall(dry: bool) -> int:
    home = claude_home()
    loc_env = home / "hera.env"
    global_settings = home / "settings.json"
    global_skills = home / "skills"
    project_settings = VAULT / ".claude" / "settings.json"
    project_disabled = project_settings.with_suffix(".json.disabled")
    global_md = home / "CLAUDE.md"
    r = Runner(dry)

    ui.header("Hera - uninstaller", {
        "vault": str(VAULT),
        "home ": str(home),
        "mode ": f"uninstall   dry={int(dry)}",
    })

    # Step 1: remove copied skills we created (tracked via manifest).
    ui.step("step 1/5: remove copied skills")
    if dry:
        ui.info(f"[dry] remove Hera skills from {global_skills} (per manifest)")
    else:
        import registration
        removed = registration.unregister_skills(VAULT, global_skills, home)
        ui.info(f"removed {removed} skill dir(s)")

    # Step 2: restore settings.json from backup, or strip our entries.
    ui.step("step 2/5: restore settings.json")
    if dry:
        ui.info(f"[dry] restore latest backup of {global_settings} or strip our hooks")
    else:
        import settings as settings_mod
        fragment = settings_mod.build_fragment(VAULT, os.name)
        if settings_mod.restore_latest_backup(global_settings):
            ui.info(f"restored {global_settings} from backup")
            settings_mod.strip_our_hooks(global_settings, fragment)
        elif global_settings.exists():
            settings_mod.strip_our_hooks(global_settings, fragment)
        else:
            ui.info("no settings.json and no backup — nothing to restore")

    # Step 3: remove locator.
    ui.step("step 3/5: remove locator")
    if not dry:
        import locator
        if locator.remove_locator(loc_env):
            ui.info(f"removed {loc_env}")
    else:
        ui.info(f"[dry] remove {loc_env}")

    # Step 4: re-enable project-local settings.json.
    ui.step("step 4/5: re-enable project-local settings.json")
    if project_disabled.exists():
        r.do(f"move {project_disabled} → {project_settings}",
             lambda: os.replace(project_disabled, project_settings))
    else:
        ui.info("(no disabled project settings to re-enable)")

    # Step 5: strip our CLAUDE.md block.
    ui.step("step 5/5: strip global CLAUDE.md block")
    if dry:
        ui.info(f"[dry] remove Hera block from {global_md}")
    elif global_md.exists() and HERA_MD_BEGIN.split("(")[0] in global_md.read_text(encoding="utf-8"):
        _remove_global_claudemd_block(global_md)
        ui.info(f"removed Hera block from {global_md}")
    else:
        ui.info("(no Hera block to remove)")

    print()
    ui.plain("uninstall: complete.")
    return 0


# --------------------------------------------------------------------------
# global CLAUDE.md block (self-contained; no lib.sh dependency)
# --------------------------------------------------------------------------

def _append_global_claudemd(target: pathlib.Path, source: pathlib.Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"cannot read vault CLAUDE.md: {source}")
    if target.exists():
        _backup(target)
    note = (
        "# This block is managed by the Hera install.py. It mirrors\n"
        "# the global CLAUDE.md so citation-learning and safety invariants\n"
        "# stay active in every directory. Remove it with: install.py --uninstall\n"
    )
    body = source.read_text(encoding="utf-8").rstrip("\n")
    block = f"{HERA_MD_BEGIN}\n{note}\n{body}\n{HERA_MD_END}\n"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    b = existing.find(HERA_MD_BEGIN)
    if b != -1:
        e = existing.find(HERA_MD_END, b)
        if e != -1:
            e_end = e + len(HERA_MD_END)
            if e_end < len(existing) and existing[e_end] == "\n":
                e_end += 1
            new = existing[:b] + block + existing[e_end:]
        else:
            new = existing + ("\n" if existing and not existing.endswith("\n") else "") + block
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        sep = "\n" if existing else ""
        new = existing + sep + block
    _atomic_write(target, new)


def _remove_global_claudemd_block(target: pathlib.Path) -> None:
    if not target.exists():
        return
    existing = target.read_text(encoding="utf-8")
    b = existing.find(HERA_MD_BEGIN)
    if b == -1:
        return
    e = existing.find(HERA_MD_END, b)
    if e == -1:
        return
    e_end = e + len(HERA_MD_END)
    if e_end < len(existing) and existing[e_end] == "\n":
        e_end += 1
    start = b
    if start >= 1 and existing[start - 1] == "\n" and (start < 2 or existing[start - 2] == "\n"):
        start -= 1
    new = existing[:start] + existing[e_end:]
    if new.strip() == "":
        target.unlink()
        return
    _atomic_write(target, new)


def _backup(path: pathlib.Path) -> pathlib.Path | None:
    """Delegate to settings.backup_file for one timestamp convention."""
    import settings as settings_mod
    return settings_mod.backup_file(path)


def _atomic_write(path: pathlib.Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=False, description="Hera installer.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--with-global-claudemd", action="store_true")
    ap.add_argument("--install-ollama", action="store_true",
                    help="install/start Ollama without prompting")
    ap.add_argument("--no-install-ollama", action="store_true",
                    help="never install/start Ollama (guide only)")
    ap.add_argument("--help", "-h", action="store_true")
    a = ap.parse_args(argv)

    if a.help:
        print(__doc__)
        return 0
    if a.uninstall:
        return do_uninstall(a.dry_run)
    # A real install needs a python that can load sqlite extensions. Dry-run
    # only prints actions, so it doesn't. Re-exec under a capable interpreter
    # if this one can't (raises SystemExit with a remedy if none is found).
    if not a.dry_run:
        _ensure_capable_interpreter(argv if argv is not None else sys.argv[1:])
    # Tri-state Ollama decision: --install-ollama forces yes, --no-install-ollama
    # forces skip, neither leaves it None (prompt on a TTY, else proceed).
    if a.install_ollama:
        ollama_yes: bool | None = True
    elif a.no_install_ollama:
        ollama_yes = False
    else:
        ollama_yes = None
    return do_install(a.dry_run, a.with_global_claudemd, ollama_yes)


if __name__ == "__main__":
    sys.exit(main())
