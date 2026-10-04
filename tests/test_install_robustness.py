"""Installer failure modes found in the production-readiness audit."""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_reinstall_after_vault_move_replaces_stale_codex_links(vault_env, tmp_path):
    """A Codex link left by the vault's previous location must not crash the
    install half-way (Claude already migrated, Codex not)."""
    run, codex_home = vault_env["run"], vault_env["codex_home"]
    (codex_home / "hera").mkdir(parents=True)
    old = tmp_path / "old-vault"
    (codex_home / "hera" / "AGENTS.md").symlink_to(old / "AGENTS.md")       # dangling
    (codex_home / "hera.env").symlink_to(tmp_path / "old-home" / "hera.env")  # dangling
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert (codex_home / "hera" / "AGENTS.md").resolve() == (vault_env["vault"] / "AGENTS.md").resolve()
    assert (codex_home / "hera.env").resolve() == (vault_env["home"] / "hera.env").resolve()


def test_foreign_regular_file_is_refused_with_a_message_not_a_traceback(vault_env):
    run, codex_home = vault_env["run"], vault_env["codex_home"]
    codex_home.mkdir(parents=True)
    (codex_home / "hera.env").write_text("SOMETHING=else\n")
    r = run()
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "hera.env" in (r.stdout + r.stderr)


def test_malformed_settings_json_stops_install_before_any_change(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True)
    (home / "settings.json").write_text('{"hooks": {},}')  # trailing comma
    r = run()
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "settings.json" in (r.stdout + r.stderr)
    assert (home / "settings.json").read_text() == '{"hooks": {},}'
    assert not (home / "hera.env").exists(), "nothing may change before the JSON is fixed"


def test_symlinked_settings_json_stays_a_symlink(vault_env, tmp_path):
    """Dotfiles managers (stow, chezmoi) keep ~/.claude/settings.json as a link."""
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True)
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"model": "x"}))
    (home / "settings.json").symlink_to(real)
    assert run().returncode == 0
    assert (home / "settings.json").is_symlink()
    assert "hooks" in json.loads(real.read_text())
    assert run("--uninstall").returncode == 0
    assert (home / "settings.json").is_symlink()
    assert json.loads(real.read_text()) == {"model": "x"}


def test_codex_is_not_registered_when_codex_is_absent(vault_env, tmp_path):
    """No ~/.codex, no codex on PATH, no CODEX_HOME: leave Codex alone."""
    vault, home = vault_env["vault"], vault_env["home"]
    fake_home = tmp_path / "fake-user-home"
    fake_home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
    env.update(CLAUDE_HOME=str(home), HOME=str(fake_home),
               PATH=os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                    if not (pathlib.Path(p) / "codex").exists()))
    import subprocess
    r = subprocess.run([sys.executable, str(vault / "install.py")], capture_output=True,
                       text=True, env=env, stdin=subprocess.DEVNULL, cwd=str(vault))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (fake_home / ".codex").exists()


def test_users_dotfiles_symlink_named_hera_env_is_not_replaced(vault_env, tmp_path):
    """Council round 2 P3: same basename is not proof the link is Hera's."""
    run, codex_home = vault_env["run"], vault_env["codex_home"]
    codex_home.mkdir(parents=True)
    mine = tmp_path / "dotfiles" / "hera.env"
    mine.parent.mkdir()
    mine.write_text("MY_OWN=1\n")
    (codex_home / "hera.env").symlink_to(mine)
    r = run()
    assert r.returncode != 0 and "Traceback" not in r.stderr
    assert (codex_home / "hera.env").resolve() == mine.resolve()
