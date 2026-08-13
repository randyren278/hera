"""Unit tests for scripts/install/ollama_provision.py.

No network, no system mutation: subprocess/urllib are monkeypatched to raise if
touched, and platform/shutil are stubbed to exercise every per-OS branch.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import ollama_provision as op  # noqa: E402


# --------------------------------------------------------------------------
# install_command() — right argv per OS / tool availability
# --------------------------------------------------------------------------

def _fake_which(present: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_install_command_windows_winget(monkeypatch):
    monkeypatch.setattr(op.platform, "system", lambda: "Windows")
    monkeypatch.setattr(op.shutil, "which", _fake_which({"winget"}))
    cmd = op.install_command()
    assert cmd is not None and cmd[:2] == ["winget", "install"]
    assert "Ollama.Ollama" in cmd


def test_install_command_windows_no_winget(monkeypatch):
    monkeypatch.setattr(op.platform, "system", lambda: "Windows")
    monkeypatch.setattr(op.shutil, "which", _fake_which(set()))
    assert op.install_command() is None                     # → .exe download path
    assert "OllamaSetup.exe" in op.install_command_text()   # but text still guides


def test_install_command_macos_brew(monkeypatch):
    monkeypatch.setattr(op.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(op.shutil, "which", _fake_which({"brew"}))
    assert op.install_command() == ["brew", "install", "ollama"]


def test_install_command_macos_no_brew(monkeypatch):
    monkeypatch.setattr(op.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(op.shutil, "which", _fake_which(set()))
    assert op.install_command() is None
    assert "brew" in op.install_command_text()  # guidance mentions the fix


def test_install_command_linux(monkeypatch):
    monkeypatch.setattr(op.platform, "system", lambda: "Linux")
    monkeypatch.setattr(op.shutil, "which", _fake_which(set()))
    cmd = op.install_command()
    assert cmd is not None and cmd[0] == "sh"
    assert "ollama.com/install.sh" in cmd[-1]


# --------------------------------------------------------------------------
# ensure_ollama — short-circuits and never mutates when it shouldn't
# --------------------------------------------------------------------------

def _forbid_mutation(monkeypatch):
    """Make any real install/daemon spawn explode, so tests prove no mutation."""
    def boom(*a, **k):
        raise AssertionError(f"unexpected subprocess call: {a}")
    monkeypatch.setattr(op.subprocess, "run", boom)
    monkeypatch.setattr(op.subprocess, "Popen", boom)


def test_ensure_ollama_daemon_up_short_circuits(monkeypatch):
    _forbid_mutation(monkeypatch)
    monkeypatch.setattr(op.ollama, "_daemon_up", lambda *a, **k: True)
    ok, detail = op.ensure_ollama(assume_yes=True)
    assert ok and "already running" in detail


def test_ensure_ollama_declined_when_no(monkeypatch):
    _forbid_mutation(monkeypatch)
    monkeypatch.setattr(op.ollama, "_daemon_up", lambda *a, **k: False)
    monkeypatch.setattr(op, "ollama_on_path", lambda: False)
    ok, detail = op.ensure_ollama(assume_yes=False)
    assert not ok and "declined" in detail


def test_ensure_ollama_prompt_no_declines(monkeypatch):
    _forbid_mutation(monkeypatch)
    monkeypatch.setattr(op.ollama, "_daemon_up", lambda *a, **k: False)
    monkeypatch.setattr(op, "ollama_on_path", lambda: False)
    ok, _ = op.ensure_ollama(assume_yes=None, prompt_fn=lambda: False)
    assert not ok


def test_ensure_ollama_dry_never_mutates(monkeypatch):
    _forbid_mutation(monkeypatch)
    monkeypatch.setattr(op.ollama, "_daemon_up", lambda *a, **k: False)
    monkeypatch.setattr(op, "ollama_on_path", lambda: False)
    monkeypatch.setattr(op.platform, "system", lambda: "Linux")
    # dry=True with assume_yes=True: walks install+start planning but the
    # _forbid_mutation guard proves nothing actually runs.
    ok, _ = op.ensure_ollama(dry=True, assume_yes=True)
    assert ok


def test_ensure_ollama_installed_but_down_tries_start(monkeypatch):
    """Binary present, daemon down → start_daemon path, dry so no real spawn."""
    _forbid_mutation(monkeypatch)
    monkeypatch.setattr(op.ollama, "_daemon_up", lambda *a, **k: False)
    monkeypatch.setattr(op, "ollama_on_path", lambda: True)
    ok, detail = op.ensure_ollama(dry=True, assume_yes=True)
    assert ok and "start" in detail.lower()
