"""Doctor hook-registration is aware of BOTH install modes (global + local).

Regression guard for the false-negative where a global install (hooks in
~/.claude/settings.json pointing at the vault, project settings.json renamed
.disabled) was reported as "no .claude/settings.json yet".
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402


def _write_global_settings(home: pathlib.Path, vault: pathlib.Path, events) -> pathlib.Path:
    home.mkdir(parents=True, exist_ok=True)
    cmd = f'"{vault}/.venv/bin/python" "{vault}/.claude/hooks/session_start.py"'
    hooks = {ev: [{"hooks": [{"type": "command", "command": cmd}]}] for ev in events}
    p = home / "settings.json"
    p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# _events_referencing_vault — the pure matcher
# --------------------------------------------------------------------------

def test_matches_only_when_command_references_this_vault(tmp_path):
    vault = tmp_path / "vault"
    other = tmp_path / "other-vault"
    home = tmp_path / "home"
    _write_global_settings(home, other, ["SessionStart", "Stop"])
    settings = home / "settings.json"
    # Hooks reference a DIFFERENT vault → not ours.
    assert brain_db._events_referencing_vault(settings, vault) == []
    # Hooks referencing this vault → detected, in canonical order.
    _write_global_settings(home, vault, ["Stop", "SessionStart"])
    assert brain_db._events_referencing_vault(settings, vault) == ["SessionStart", "Stop"]


def test_missing_settings_returns_empty(tmp_path):
    assert brain_db._events_referencing_vault(tmp_path / "nope.json", tmp_path) == []


# --------------------------------------------------------------------------
# _doctor_hooks — the three states
# --------------------------------------------------------------------------

def test_global_install_reports_registered(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _write_global_settings(home, REPO, ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"])
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    brain_db._doctor_hooks()
    out = capsys.readouterr().out
    assert "registered globally" in out
    assert "SessionStart" in out


def test_no_registration_but_disabled_gives_honest_message(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"          # empty — no global hooks
    home.mkdir()
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    # SETTINGS is REPO/.claude/settings.json — absent in the source tree, but its
    # .disabled sibling exists (this template is a global-install artifact).
    disabled = brain_db.SETTINGS.with_suffix(".json.disabled")
    if not disabled.exists():
        # Only assert the fallback wording when the disabled marker is present;
        # otherwise assert the generic "no hooks" message. Either is honest.
        brain_db._doctor_hooks()
        assert "no hooks registered" in capsys.readouterr().out
        return
    brain_db._doctor_hooks()
    out = capsys.readouterr().out
    assert "not registered for THIS vault" in out
    assert "install.py" in out
