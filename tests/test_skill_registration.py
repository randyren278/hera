"""CP-3: skills are copied into ~/.claude/skills, discoverable, clobber-safe."""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import registration  # noqa: E402

SKILLS = ["hera-setup", "hera-ingest", "hera-conflicts", "hera-prune", "hera-team"]


def test_skills_registered_and_discoverable(vault_env):
    r = vault_env["run"]()
    assert r.returncode == 0, r.stderr
    skills = vault_env["home"] / "skills"
    for name in SKILLS:
        skill_md = skills / name / "SKILL.md"
        assert skill_md.is_file(), f"SKILL.md not discoverable for {name}"


def test_manifest_records_created_skills(vault_env):
    vault_env["run"]()
    manifest = vault_env["home"] / registration.MANIFEST_NAME
    assert manifest.exists()
    import json
    data = json.loads(manifest.read_text())
    assert set(data["skills"]) == set(SKILLS)


def test_reinstall_is_idempotent(vault_env):
    r1 = vault_env["run"]()
    assert r1.returncode == 0, r1.stderr
    r2 = vault_env["run"]()
    assert r2.returncode == 0, r2.stderr
    skills = vault_env["home"] / "skills"
    assert (skills / "hera-setup" / "SKILL.md").is_file()


def test_refuses_to_clobber_foreign_dir(tmp_path):
    """A pre-existing non-managed skill dir is never overwritten."""
    vault = tmp_path / "vault"
    (vault / ".claude" / "skills" / "hera-setup").mkdir(parents=True)
    (vault / ".claude" / "skills" / "hera-setup" / "SKILL.md").write_text("x")
    home = tmp_path / "home"
    skills = home / "skills"
    foreign = skills / "hera-setup"
    foreign.mkdir(parents=True)
    (foreign / "user-file.md").write_text("do not delete me")

    import pytest
    with pytest.raises(RuntimeError, match="non-managed"):
        registration.register_skills(vault, skills, ["hera-setup"], home)
    # Foreign content untouched.
    assert (foreign / "user-file.md").read_text() == "do not delete me"
