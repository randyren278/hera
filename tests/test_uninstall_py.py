"""CP-3: install → uninstall leaves no Hera residue (roundtrip)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


def _read_json(p: pathlib.Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_uninstall_roundtrip(vault_env):
    run, home, proj = vault_env["run"], vault_env["home"], vault_env["proj"]

    # Install.
    r = run()
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"

    settings = home / "settings.json"
    locator = home / "hera.env"
    skills = home / "skills"

    # Post-install invariants.
    assert settings.exists(), "global settings.json not written"
    hooks = _read_json(settings)["hooks"]
    assert set(hooks) >= {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    assert locator.exists(), "locator not written"
    assert (skills / "hera-setup").is_dir()
    # Project settings disabled so hooks don't double-fire.
    assert not proj.exists(), "project settings.json should be disabled"
    assert proj.with_suffix(".json.disabled").exists()

    # Uninstall.
    r = run("--uninstall")
    assert r.returncode == 0, f"uninstall failed:\n{r.stdout}\n{r.stderr}"

    # Post-uninstall: no residue.
    # settings.json held only our hooks → removed entirely.
    assert not settings.exists(), "settings.json should be gone (held only our hooks)"
    assert not locator.exists(), "locator should be removed"
    for name in ("hera-setup", "hera-ingest", "hera-conflicts", "hera-prune", "hera-team"):
        assert not (skills / name).exists(), f"skill {name} not removed"
    # Project settings re-enabled.
    assert proj.exists(), "project settings.json should be re-enabled"
    assert not proj.with_suffix(".json.disabled").exists()


def test_uninstall_preserves_foreign_settings(vault_env):
    """A pre-existing foreign settings.json is restored, not deleted."""
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True, exist_ok=True)
    settings = home / "settings.json"
    settings.write_text(json.dumps({
        "model": "claude-opus",
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "foreign-tool"}]}]},
    }) + "\n", encoding="utf-8")

    r = run()
    assert r.returncode == 0, r.stderr
    r = run("--uninstall")
    assert r.returncode == 0, r.stderr

    # Foreign content survives; our hooks stripped.
    assert settings.exists(), "foreign settings.json must not be deleted"
    data = _read_json(settings)
    assert data["model"] == "claude-opus"
    cmds = [h["command"] for g in data["hooks"].get("SessionStart", []) for h in g["hooks"]]
    assert "foreign-tool" in cmds
    assert not any(".venv" in c for c in cmds), "our hook command should be stripped"


def test_uninstall_keeps_settings_changed_after_install(vault_env):
    """Settings the user changed AFTER install survive uninstall.

    Regression: uninstall used to copy the newest settings.json.hera-backup.*
    over settings.json, silently reverting every change made since install
    (new hooks, statusline, permissions).
    """
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True, exist_ok=True)
    settings = home / "settings.json"
    settings.write_text(json.dumps({"model": "old"}) + "\n", encoding="utf-8")

    assert run().returncode == 0
    data = _read_json(settings)
    data["model"] = "new"
    data["statusLine"] = {"type": "command", "command": "added-later"}
    settings.write_text(json.dumps(data) + "\n", encoding="utf-8")

    assert run("--uninstall").returncode == 0
    data = _read_json(settings)
    assert data["model"] == "new"
    assert data["statusLine"]["command"] == "added-later"
    assert not any(".venv" in h["command"]
                   for groups in data.get("hooks", {}).values()
                   for g in groups for h in g["hooks"])
    # Backups are left on disk for manual recovery, never deleted.
    assert list(home.glob("settings.json.hera-backup.*"))


def test_uninstall_ignores_polluted_backup(vault_env):
    """A newest backup that already contains Hera hooks can't resurrect them."""
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True, exist_ok=True)
    settings = home / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": "users-own-hook"}]}]}}) + "\n",
        encoding="utf-8")
    assert run().returncode == 0
    (home / "settings.json.hera-backup.99999999-999999").write_text(
        settings.read_text(encoding="utf-8"), encoding="utf-8")

    assert run("--uninstall").returncode == 0
    text = settings.read_text(encoding="utf-8")
    assert "users-own-hook" in text
    assert ".claude/hooks/" not in text


def test_install_supersedes_other_vault(vault_env, tmp_path):
    """Installing vault B removes hooks a previous install of vault A left."""
    run, home = vault_env["run"], vault_env["home"]
    import sys
    sys.path.insert(0, str(vault_env["vault"] / "scripts" / "install"))
    import settings as settings_mod
    other = tmp_path / "other vault"
    settings_mod.merge_settings(home / "settings.json",
                                settings_mod.build_fragment(other))

    r = run()
    assert r.returncode == 0, r.stderr
    assert "other vault" in r.stdout  # the user is told what was replaced
    vaults = settings_mod.hera_vaults_in_settings(home / "settings.json")
    assert vaults == [vault_env["vault"].as_posix()]


def test_uninstall_leaves_a_shipped_disabled_project_settings_alone(vault_env):
    """A fresh clone ships .claude/settings.json.disabled (tracked). Install has
    nothing to disable, so uninstall must not rename it into an active,
    relative-path project settings.json (dirtying the checkout)."""
    run, proj = vault_env["run"], vault_env["proj"]
    proj.rename(proj.with_suffix(".json.disabled"))
    assert run().returncode == 0
    assert run("--uninstall").returncode == 0
    assert not proj.exists()
    assert proj.with_suffix(".json.disabled").exists()


def test_uninstall_from_an_inactive_vault_leaves_the_active_one_alone(vault_env, tmp_path):
    """Council round 2 P2: `other/install.py --uninstall` removed the active
    vault's skills and the shared locator while its hooks kept firing."""
    run, home, vault = vault_env["run"], vault_env["home"], vault_env["vault"]
    assert run().returncode == 0  # this vault is active
    other = tmp_path / "other"
    import shutil
    shutil.copytree(vault, other, symlinks=True)
    env = dict(os.environ, CLAUDE_HOME=str(home), CODEX_HOME=str(vault_env["codex_home"]))
    r = subprocess.run([sys.executable, str(other / "install.py"), "--uninstall"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / "hera.env").exists()
    assert (home / "skills" / "hera-ingest").resolve().is_relative_to(vault)
    assert (vault_env["codex_home"] / "hera.env").exists()


def test_uninstall_validates_json_before_removing_anything(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    assert run().returncode == 0
    (home / "settings.json").write_text('{"hooks": {},}')
    r = run("--uninstall")
    assert r.returncode != 0 and "Traceback" not in r.stderr
    assert (home / "skills" / "hera-ingest").exists(), "removed skills before failing"
