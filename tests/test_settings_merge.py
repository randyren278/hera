"""CP-2: settings fragment generation + merge/strip is idempotent."""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import settings as settings_mod  # noqa: E402

VAULT = pathlib.Path("/Users/dev/vault")


def test_build_fragment_shape():
    frag = settings_mod.build_fragment(VAULT, "posix")
    hooks = frag["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    # Per-event metadata carried through.
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 10
    assert hooks["Stop"][0]["hooks"][0]["async"] is True
    assert hooks["SessionEnd"][0]["hooks"][0]["timeout"] == 600


def test_merge_into_empty_creates(tmp_path):
    target = tmp_path / "settings.json"
    frag = settings_mod.build_fragment(VAULT, "posix")
    settings_mod.merge_settings(target, frag)
    data = json.loads(target.read_text())
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}


def test_merge_idempotent(tmp_path):
    target = tmp_path / "settings.json"
    frag = settings_mod.build_fragment(VAULT, "posix")
    settings_mod.merge_settings(target, frag)
    first = target.read_text()
    settings_mod.merge_settings(target, frag)
    second = target.read_text()
    assert first == second
    # Exactly one group per event — no duplication.
    data = json.loads(second)
    for ev in data["hooks"]:
        assert len(data["hooks"][ev]) == 1


def test_merge_preserves_foreign_keys(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "model": "claude-opus",
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
    }))
    frag = settings_mod.build_fragment(VAULT, "posix")
    settings_mod.merge_settings(target, frag)
    data = json.loads(target.read_text())
    assert data["model"] == "claude-opus"
    # Foreign SessionStart group preserved AND ours appended.
    cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "other-tool" in cmds
    assert any(".venv/bin/python" in c for c in cmds)


def test_has_our_hooks(tmp_path):
    target = tmp_path / "settings.json"
    frag = settings_mod.build_fragment(VAULT, "posix")
    assert settings_mod.settings_has_our_hooks(target, frag) is False
    settings_mod.merge_settings(target, frag)
    assert settings_mod.settings_has_our_hooks(target, frag) is True


def test_strip_roundtrip_deletes_our_only_file(tmp_path):
    target = tmp_path / "settings.json"
    frag = settings_mod.build_fragment(VAULT, "posix")
    settings_mod.merge_settings(target, frag)
    settings_mod.strip_our_hooks(target, frag)
    # File contained only our hooks → removed.
    assert not target.exists()


def test_strip_preserves_foreign(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "model": "x",
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
    }))
    frag = settings_mod.build_fragment(VAULT, "posix")
    settings_mod.merge_settings(target, frag)
    settings_mod.strip_our_hooks(target, frag)
    data = json.loads(target.read_text())
    assert data["model"] == "x"
    cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
    assert cmds == ["other-tool"]


def test_backup_and_restore(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{"v":1}')
    backup = settings_mod.backup_file(target)
    assert backup is not None and backup.exists()
    target.write_text('{"v":2}')
    assert settings_mod.restore_latest_backup(target) is True
    assert json.loads(target.read_text())["v"] == 1


def test_backup_absent_is_none(tmp_path):
    assert settings_mod.backup_file(tmp_path / "nope.json") is None
    assert settings_mod.restore_latest_backup(tmp_path / "nope.json") is False
