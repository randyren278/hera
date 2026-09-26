"""Hera skills stay linked to their canonical source on POSIX."""
from __future__ import annotations

from conftest import count_symlinks


def test_install_links_skills(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"
    home = vault_env["home"]
    links = count_symlinks(home)
    assert len(links) == 5


def test_skills_resolve_to_vault(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, r.stderr
    skills = vault_env["home"] / "skills"
    for name in ("hera-setup", "hera-ingest", "hera-conflicts", "hera-prune", "hera-team"):
        d = skills / name
        assert d.is_dir(), f"missing skill dir {d}"
        assert d.is_symlink(), f"skill {name} should be a symlink"


def test_no_global_hooks_mirror(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, r.stderr
    # Hooks run in-repo via absolute command strings; no ~/.claude/hooks mirror.
    assert not (vault_env["home"] / "hooks").exists()
