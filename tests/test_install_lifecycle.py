"""Reinstall + global CLAUDE.md lifecycle, against an isolated vault copy.

Hermetic replacements for the legacy check_reinstall_backup.sh and
test_install_global_md.sh, which ran the real installer against the live
checkout (and exercised the retired bash lib.sh, not install.py).
"""
from __future__ import annotations

import json

BEGIN = "# >>> Hera (managed by install.py) >>>"


def test_reinstall_keeps_the_clean_pre_install_backup(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"model": "mine"}), encoding="utf-8")
    assert run().returncode == 0
    assert run().returncode == 0
    backups = list(home.glob("settings.json.hera-backup.*"))
    assert len(backups) == 1, backups
    assert ".venv" not in backups[0].read_text(encoding="utf-8")


def test_global_claudemd_append_refresh_and_remove(vault_env):
    run, home, vault = vault_env["run"], vault_env["home"], vault_env["vault"]
    home.mkdir(parents=True, exist_ok=True)
    md = home / "CLAUDE.md"
    md.write_text("USER GLOBAL RULES\n", encoding="utf-8")

    assert run("--with-global-claudemd").returncode == 0
    text = md.read_text(encoding="utf-8")
    assert text.startswith("USER GLOBAL RULES\n") and text.count(BEGIN) == 1

    # A changed vault CLAUDE.md is refreshed in place on reinstall — never
    # appended twice, never left stale.
    (vault / "CLAUDE.md").write_text("NEW VAULT RULES\n", encoding="utf-8")
    assert run("--with-global-claudemd").returncode == 0
    text = md.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and "NEW VAULT RULES" in text

    assert run("--uninstall").returncode == 0
    assert md.read_text(encoding="utf-8") == "USER GLOBAL RULES\n"


def test_global_claudemd_skipped_without_flag_off_tty(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    assert run().returncode == 0
    assert not (home / "CLAUDE.md").exists()


def test_dry_run_with_global_claudemd_writes_nothing(vault_env):
    run, home = vault_env["run"], vault_env["home"]
    r = run("--dry-run", "--with-global-claudemd")
    assert r.returncode == 0, r.stderr
    assert "global CLAUDE.md" in r.stdout
    assert not (home / "CLAUDE.md").exists()
    assert vault_env["proj"].exists(), "dry-run must not disable project settings"
