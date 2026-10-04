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
import hera_db  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_codex(tmp_path, monkeypatch):
    """Keep the developer's real ~/.codex out of every doctor test."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))


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
    assert hera_db._events_referencing_vault(settings, vault) == []
    # Hooks referencing this vault → detected, in canonical order.
    _write_global_settings(home, vault, ["Stop", "SessionStart"])
    assert hera_db._events_referencing_vault(settings, vault) == ["SessionStart", "Stop"]


def test_missing_settings_returns_empty(tmp_path):
    assert hera_db._events_referencing_vault(tmp_path / "nope.json", tmp_path) == []


# --------------------------------------------------------------------------
# _doctor_hooks — the three states
# --------------------------------------------------------------------------

def test_global_install_reports_registered(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _write_global_settings(home, REPO, ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"])
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    hera_db._doctor_hooks()
    out = capsys.readouterr().out
    assert "registered globally" in out
    assert "SessionStart" in out


def test_no_registration_but_disabled_gives_honest_message(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"          # empty — no global hooks
    home.mkdir()
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    # SETTINGS is REPO/.claude/settings.json — absent in the source tree, but its
    # .disabled sibling exists (this template is a global-install artifact).
    disabled = hera_db.SETTINGS.with_suffix(".json.disabled")
    if not disabled.exists():
        # Only assert the fallback wording when the disabled marker is present;
        # otherwise assert the generic "no hooks" message. Either is honest.
        hera_db._doctor_hooks()
        assert "no hooks registered" in capsys.readouterr().out
        return
    hera_db._doctor_hooks()
    out = capsys.readouterr().out
    assert "not registered for THIS vault" in out
    assert "install.py" in out


# --------------------------------------------------------------------------
# another vault's hooks / locator drift — the double-fire misconfiguration
# --------------------------------------------------------------------------

def _add_vault_hooks(settings: pathlib.Path, vault: pathlib.Path) -> None:
    sys.path.insert(0, str(REPO / "scripts" / "install"))
    import settings as settings_mod
    settings_mod.merge_settings(settings, settings_mod.build_fragment(vault))


def test_other_vault_hooks_fail_doctor(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    settings = home / "settings.json"
    _add_vault_hooks(settings, REPO)
    _add_vault_hooks(settings, tmp_path / "second brain" / "hera")
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    out = capsys.readouterr().out
    assert state["fail"] is True
    assert "second brain" in out and "install.py" in out


def test_single_vault_install_passes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    _add_vault_hooks(home / "settings.json", REPO)
    (home / "hera.env").write_text(f'HERA_VAULT="{REPO}"\n', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    assert state["fail"] is False, capsys.readouterr().out


def test_locator_pointing_elsewhere_fails(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    _add_vault_hooks(home / "settings.json", REPO)
    (home / "hera.env").write_text('HERA_VAULT="/somewhere/else"\n', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    out = capsys.readouterr().out
    assert state["fail"] is True and "/somewhere/else" in out


def _codex_hooks(codex_home: pathlib.Path, vault: pathlib.Path) -> None:
    sys.path.insert(0, str(REPO / "scripts" / "install"))
    import codex
    import settings as settings_mod
    codex_home.mkdir(parents=True, exist_ok=True)
    settings_mod.merge_settings(codex_home / "hooks.json", codex.fragment(vault))


def test_codex_on_a_different_vault_fails(tmp_path, monkeypatch, capsys):
    """Claude and Codex must share one vault."""
    home, codex_home = tmp_path / "home", tmp_path / "codex"
    home.mkdir()
    _add_vault_hooks(home / "settings.json", REPO)
    _codex_hooks(codex_home, tmp_path / "elsewhere")
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    out = capsys.readouterr().out
    assert state["fail"] is True and "Codex" in out and "elsewhere" in out


def test_codex_on_same_vault_passes(tmp_path, monkeypatch, capsys):
    home, codex_home = tmp_path / "home", tmp_path / "codex"
    home.mkdir()
    _add_vault_hooks(home / "settings.json", REPO)
    _codex_hooks(codex_home, REPO)
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    out = capsys.readouterr().out
    assert state["fail"] is False, out
    assert "Codex" in out


def test_vault_path_prefix_is_not_a_match(tmp_path):
    """/x/hera must not claim hooks registered for /x/hera-old."""
    home = tmp_path / "home"
    _write_global_settings(home, tmp_path / "hera-old", ["SessionStart"])
    assert hera_db._events_referencing_vault(home / "settings.json", tmp_path / "hera") == []


def test_doctor_lists_orphan_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hera_db, "REPO", tmp_path)
    monkeypatch.setattr(hera_db, "WIKI", tmp_path / "wiki")
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "sources" / "Lost.md").write_text("---\nid: X\n---\n")
    conn = hera_db.ensure_ready(tmp_path / "h.db")
    monkeypatch.setattr(hera_db, "ensure_ready", lambda *a, **k: conn)
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "home"))
    hera_db.doctor()
    assert "orphan pages (1)" in capsys.readouterr().out


def test_only_another_vault_registered_fails(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    _add_vault_hooks(home / "settings.json", tmp_path / "elsewhere")
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    state = {"fail": False}
    hera_db._doctor_hooks(state)
    assert state["fail"] is True and "elsewhere" in capsys.readouterr().out
