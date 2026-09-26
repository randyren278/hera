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
    locator = home / "hera.env"
    assert locator.exists()
    text = locator.read_text()
    assert "HERA_VAULT=" in text
    assert not any(l.lstrip().startswith("export ") for l in text.splitlines())

    # 2) settings.json has all four hook events, each an in-repo command.
    settings = home / "settings.json"
    hooks = json.loads(settings.read_text())["hooks"]
    for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        cmd = hooks[ev][0]["hooks"][0]["command"]
        assert "hera_cli.py" not in cmd  # hooks call the script directly
        assert ".claude/hooks/" in cmd or r".claude\hooks" in cmd
        assert "&&" not in cmd and "$" not in cmd  # cmd.exe-safe

    # 3) both agents resolve skills to one live source.
    skills = home / "skills"
    for name in ("hera-setup", "hera-ingest", "hera-conflicts", "hera-prune", "hera-team"):
        assert (skills / name / "SKILL.md").is_file()
        assert (skills / name).resolve() == (vault_env["vault"] / ".claude" / "skills" / name).resolve()
        assert (vault_env["codex_home"] / "skills" / name).resolve() == (skills / name).resolve()
    assert (vault_env["codex_home"] / "hera.env").resolve() == locator.resolve()
    assert (vault_env["codex_home"] / "hera" / "AGENTS.md").resolve() == (vault_env["vault"] / "AGENTS.md").resolve()
    assert len(json.loads((vault_env["codex_home"] / "hooks.json").read_text())["hooks"]) == 4

    # 4) project settings disabled.
    assert not proj.exists()
    assert proj.with_suffix(".json.disabled").exists()

    # Uninstall clean.
    r = run("--uninstall")
    assert r.returncode == 0, f"uninstall failed:\n{r.stdout}\n{r.stderr}"
    assert not locator.exists()
    assert not settings.exists()
    assert proj.exists()
    assert not (vault_env["codex_home"] / "hooks.json").exists()
    assert not (vault_env["codex_home"] / "skills" / "hera-ingest").exists()


def test_dry_run_changes_nothing(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    r = run("--dry-run")
    assert r.returncode == 0, r.stderr
    # Dry run must not create the home artifacts.
    assert not (home / "hera.env").exists()
    assert not (home / "settings.json").exists()
    assert not (home / "skills").exists()
    assert not (vault_env["codex_home"] / "skills").exists()
