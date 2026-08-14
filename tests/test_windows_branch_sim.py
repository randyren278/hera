"""CP-8: os.name-forced Windows-branch simulation.

Proves the Windows code paths in hookcmd, venv-resolve, settings generation,
and skill registration take the ``nt`` branch and produce valid Windows-shaped
artifacts — exercised on a POSIX CI host by forcing ``os.name``/the os_name
argument, so the Windows behaviour is verified without a Windows machine.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
sys.path.insert(0, str(REPO / "scripts"))
import hookcmd  # noqa: E402
import settings as settings_mod  # noqa: E402
import venv as venv_mod  # noqa: E402
import registration  # noqa: E402

WIN_VAULT = pathlib.Path(r"C:\Users\dev\vault")


def test_venv_resolve_takes_windows_branch():
    py = venv_mod.venv_python(WIN_VAULT, os_name="nt")
    assert py == WIN_VAULT / ".venv" / "Scripts" / "python.exe"


def test_hookcmd_windows_shape_for_all_events():
    for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        cmd = hookcmd.hook_command("nt", WIN_VAULT, ev)
        # Windows interpreter + backslash separators, no posixisms.
        assert r"\.venv\Scripts\python.exe" in cmd
        assert "/" not in cmd
        assert not hookcmd.has_posixisms(cmd)


def test_settings_fragment_windows_commands():
    frag = settings_mod.build_fragment(WIN_VAULT, os_name="nt")
    for ev, groups in frag["hooks"].items():
        cmd = groups[0]["hooks"][0]["command"]
        assert "python.exe" in cmd
        assert not hookcmd.has_posixisms(cmd)
    # Metadata still attached on the Windows branch.
    assert frag["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 10
    assert frag["hooks"]["Stop"][0]["hooks"][0]["async"] is True


def test_windows_merge_roundtrip(tmp_path):
    """A Windows-generated fragment merges + strips cleanly (idempotent),
    exercised on this host."""
    target = tmp_path / "settings.json"
    frag = settings_mod.build_fragment(WIN_VAULT, os_name="nt")
    settings_mod.merge_settings(target, frag)
    assert settings_mod.settings_has_our_hooks(target, frag)
    settings_mod.merge_settings(target, frag)  # idempotent
    data = json.loads(target.read_text())
    for ev in data["hooks"]:
        assert len(data["hooks"][ev]) == 1
    settings_mod.strip_our_hooks(target, frag)
    assert not target.exists()  # was our-only


def test_registration_is_copy_not_symlink(tmp_path):
    """Registration is OS-neutral by design (copytree, never symlink) — that IS
    the Windows-correct behaviour (no Developer Mode needed). Verify the copy +
    manifest round-trip. (We don't force os.name='nt' here: that would make
    pathlib instantiate WindowsPath on a POSIX host, which Python forbids;
    registration.py has no os.name branch to exercise.)"""
    vault = tmp_path / "vault"
    (vault / ".claude" / "skills" / "hera-setup").mkdir(parents=True)
    (vault / ".claude" / "skills" / "hera-setup" / "SKILL.md").write_text("x")
    home = tmp_path / "home"
    skills = home / "skills"

    registered = registration.register_skills(vault, skills, ["hera-setup"], home)
    assert registered == ["hera-setup"]
    dst = skills / "hera-setup"
    assert dst.is_dir() and not dst.is_symlink()
    manifest = json.loads((home / registration.MANIFEST_NAME).read_text())
    assert "hera-setup" in manifest["skills"]
    assert registration.unregister_skills(vault, skills, home) == 1
    assert not dst.exists()


def test_hera_cli_windows_interpreter(monkeypatch):
    """hera_cli resolves the Windows venv interpreter when os.name is nt."""
    import hera_cli
    monkeypatch.setattr(os, "name", "nt")
    py = hera_cli._venv_python(WIN_VAULT)
    assert py == WIN_VAULT / ".venv" / "Scripts" / "python.exe"


def test_session_end_file_detach_flags_windows(monkeypatch):
    """session_end_file uses Windows detach creationflags, never start_new_session,
    when os.name is nt."""
    hooks_dir = REPO / ".claude" / "hooks"
    sys.path.insert(0, str(hooks_dir))
    import importlib
    import session_end_file
    importlib.reload(session_end_file)

    monkeypatch.setattr(os, "name", "nt")
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.delenv("HERA_FILING_SYNC", raising=False)
    monkeypatch.delenv("HERA_OFF", raising=False)

    # Force the hook (not CLI) branch: main() takes the CLI path when argv has
    # >=3 elements, so pin argv to the bare hook name.
    monkeypatch.setattr(sys, "argv", ["session_end_file.py"])

    # Feed a hook event on stdin (transcript path need not exist — the async
    # branch forks a worker without touching the file itself).
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"transcript_path": "/tmp/does-not-matter.jsonl",
                    "session_id": "winsim"})))

    rc = session_end_file.main()
    assert rc == 0
    kw = captured.get("kwargs", {})
    assert captured.get("cmd"), "Popen was never called on the async branch"
    assert "start_new_session" not in kw, "POSIX-only flag used on Windows branch"
    assert kw.get("creationflags"), "expected Windows creationflags"
