"""A second install from a different vault path must supersede the first.

Regression: ~/.claude/settings.json on a real machine carried hook groups for
two vaults ("/…/hera" and "/…/second brain/hera"). merge_settings deduped only
by exact command string, so every hook fired twice and every session was filed
into both vaults.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import settings as settings_mod  # noqa: E402

OLD = pathlib.Path("/Users/dev/second brain/hera")
NEW = pathlib.Path("/Users/dev/hera")


def _cmds(data: dict, event: str) -> list[str]:
    return [h["command"] for g in data["hooks"].get(event, []) for h in g["hooks"]]


def test_hera_hook_vault_recognises_every_generation():
    v = settings_mod.hera_hook_vault
    for os_name in ("posix", "nt"):
        frag = settings_mod.build_fragment(OLD, os_name)
        for groups in frag["hooks"].values():
            cmd = groups[0]["hooks"][0]["command"]
            assert v(cmd) is not None, cmd
    assert v('"/x/v/.venv/bin/python" "/x/v/scripts/codex_hook.py" prompt') == "/x/v"
    legacy = ('. "$HOME/.claude/hera.env" && "$HERA_VAULT/.venv/bin/python" '
              '"$HOME/.claude/hooks/session_start.py"')
    assert v(legacy) is not None
    assert v("claudetui hook session-heatmap") is None
    assert v('"/x/.venv/bin/python" "/x/scripts/other.py"') is None


def test_install_from_second_vault_removes_first(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": "claudetui hook x"}]}]}}))
    settings_mod.merge_settings(target, settings_mod.build_fragment(OLD, "posix"))

    removed = settings_mod.remove_other_vault_hooks(target, NEW)
    settings_mod.merge_settings(target, settings_mod.build_fragment(NEW, "posix"))

    assert removed == [OLD.as_posix()]
    data = json.loads(target.read_text())
    for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        hera = [c for c in _cmds(data, ev) if settings_mod.hera_hook_vault(c)]
        assert len(hera) == 1 and str(NEW) in hera[0], (ev, hera)
    assert "claudetui hook x" in _cmds(data, "SessionStart")


def test_remove_other_vault_hooks_keeps_this_vault_and_is_noop_when_clean(tmp_path):
    target = tmp_path / "settings.json"
    settings_mod.merge_settings(target, settings_mod.build_fragment(NEW, "posix"))
    before = target.read_text()
    assert settings_mod.remove_other_vault_hooks(target, NEW) == []
    assert target.read_text() == before
    assert settings_mod.remove_other_vault_hooks(tmp_path / "absent.json", NEW) == []


def test_remove_other_vault_hooks_windows_paths(tmp_path):
    target = tmp_path / "settings.json"
    settings_mod.merge_settings(target, settings_mod.build_fragment(OLD, "nt"))
    settings_mod.merge_settings(target, settings_mod.build_fragment(NEW, "nt"))
    removed = settings_mod.remove_other_vault_hooks(target, NEW)
    assert len(removed) == 1
    data = json.loads(target.read_text())
    assert all("second brain" not in c for c in _cmds(data, "Stop"))
    assert len(_cmds(data, "Stop")) == 1


def test_hera_vaults_in_settings_lists_each_vault(tmp_path):
    target = tmp_path / "settings.json"
    settings_mod.merge_settings(target, settings_mod.build_fragment(OLD, "posix"))
    settings_mod.merge_settings(target, settings_mod.build_fragment(NEW, "posix"))
    assert settings_mod.hera_vaults_in_settings(target) == sorted(
        [OLD.as_posix(), NEW.as_posix()])


def test_users_own_hook_named_like_ours_is_never_touched(tmp_path):
    """Council round 2 P1: a user's own ~/.claude/hooks/session_start.py run by
    a system python is not a Hera hook — Hera hooks run under their own
    vault's .venv interpreter."""
    v = settings_mod.hera_hook_vault
    own = '"/usr/bin/python3" "/Users/me/.claude/hooks/session_start.py"'
    assert v(own) is None
    assert v('"/Users/me/.venv/bin/python" "/Users/other/.claude/hooks/stop_score.py"') is None
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": own}]}]}}))
    assert settings_mod.remove_other_vault_hooks(target, NEW) == []
    cmds = [h["command"] for g in json.loads(target.read_text())["hooks"]["SessionStart"]
            for h in g["hooks"]]
    assert cmds == [own]


def test_home_level_venv_user_hook_is_not_a_vault(tmp_path):
    """Council round 3 P3: "~/.venv/bin/python" "~/.claude/hooks/session_start.py"
    matched the same-vault-interpreter rule with vault == $HOME."""
    home = tmp_path / "me"
    (home / ".venv" / "bin").mkdir(parents=True)
    cmd = f'"{home}/.venv/bin/python" "{home}/.claude/hooks/session_start.py"'
    assert settings_mod.hera_hook_vault(cmd) is None
    vault = tmp_path / "vault"
    (vault / "scripts").mkdir(parents=True)
    (vault / "scripts" / "hera_db.py").write_text("")
    real = f'"{vault}/.venv/bin/python" "{vault}/.claude/hooks/session_start.py"'
    assert settings_mod.hera_hook_vault(real) == vault.as_posix()
