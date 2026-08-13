"""CP-3: install → uninstall leaves no second-brain residue (roundtrip)."""
from __future__ import annotations

import json
import pathlib


def _read_json(p: pathlib.Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_uninstall_roundtrip(vault_env):
    run, home, proj = vault_env["run"], vault_env["home"], vault_env["proj"]

    # Install.
    r = run()
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"

    settings = home / "settings.json"
    locator = home / "second-brain.env"
    skills = home / "skills"

    # Post-install invariants.
    assert settings.exists(), "global settings.json not written"
    hooks = _read_json(settings)["hooks"]
    assert set(hooks) >= {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    assert locator.exists(), "locator not written"
    assert (skills / "brain-setup").is_dir()
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
    for name in ("brain-setup", "brain-ingest", "brain-conflicts", "brain-prune", "brain-team"):
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
