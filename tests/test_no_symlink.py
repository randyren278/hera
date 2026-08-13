"""CP-3: install creates ZERO symlinks anywhere under ~/.claude."""
from __future__ import annotations

from conftest import count_symlinks


def test_install_creates_no_symlink(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"
    home = vault_env["home"]
    links = count_symlinks(home)
    assert links == [], f"install created symlinks under {home}: {links}"


def test_skills_are_real_dirs_not_links(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, r.stderr
    skills = vault_env["home"] / "skills"
    for name in ("brain-setup", "brain-ingest", "brain-conflicts", "brain-prune", "brain-team"):
        d = skills / name
        assert d.is_dir(), f"missing skill dir {d}"
        assert not d.is_symlink(), f"skill {name} is a symlink — should be a copy"


def test_no_global_hooks_mirror(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, r.stderr
    # Hooks run in-repo via absolute command strings; no ~/.claude/hooks mirror.
    assert not (vault_env["home"] / "hooks").exists()
