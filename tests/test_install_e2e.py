"""CP-FINAL sc1: full install integration against an isolated vault copy.

Runs install.py end-to-end (real, not dry-run) against a copied vault with a
fake CLAUDE_HOME, asserts the produced artifacts, then uninstalls clean.
Uses the shared vault_env fixture (see conftest.py).
"""
from __future__ import annotations

import json


def test_install_produces_all_artifacts(vault_env):
    run, home, proj = vault_env["run"], vault_env["home"], vault_env["proj"]

    r = run()
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"

    # 1) locator written, plain KEY=VALUE, no export.
    locator = home / "second-brain.env"
    assert locator.exists()
    text = locator.read_text()
    assert "SECOND_BRAIN_VAULT=" in text
    assert not any(l.lstrip().startswith("export ") for l in text.splitlines())

    # 2) settings.json has all four hook events, each an in-repo command.
    settings = home / "settings.json"
    hooks = json.loads(settings.read_text())["hooks"]
    for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        cmd = hooks[ev][0]["hooks"][0]["command"]
        assert "brain_cli.py" not in cmd  # hooks call the script directly
        assert ".claude/hooks/" in cmd or r".claude\hooks" in cmd
        assert "&&" not in cmd and "$" not in cmd  # cmd.exe-safe

    # 3) skills copied (real dirs), no symlink anywhere.
    skills = home / "skills"
    for name in ("brain-setup", "brain-ingest", "brain-conflicts", "brain-prune", "brain-team"):
        assert (skills / name / "SKILL.md").is_file()
        assert not (skills / name).is_symlink()
    assert not any(p.is_symlink() for p in home.rglob("*"))

    # 4) project settings disabled.
    assert not proj.exists()
    assert proj.with_suffix(".json.disabled").exists()

    # Uninstall clean.
    r = run("--uninstall")
    assert r.returncode == 0, f"uninstall failed:\n{r.stdout}\n{r.stderr}"
    assert not locator.exists()
    assert not settings.exists()
    assert proj.exists()


def test_dry_run_changes_nothing(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    r = run("--dry-run")
    assert r.returncode == 0, r.stderr
    # Dry run must not create the home artifacts.
    assert not (home / "second-brain.env").exists()
    assert not (home / "settings.json").exists()
    assert not (home / "skills").exists()
