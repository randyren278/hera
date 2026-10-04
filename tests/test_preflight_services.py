"""Runtime services (Ollama, agent CLI) warn in preflight; they never block.

Regression: `install.py --no-install-ollama` on a machine without Ollama built
the venv and then exited 1 at preflight, registering nothing — although the
README tells that user to start Ollama themselves afterwards, and every hook
already degrades gracefully without it.
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import preflight  # noqa: E402


def _no_services(monkeypatch):
    monkeypatch.setattr(preflight, "_check_ollama_daemon", lambda: (False, "down"))
    monkeypatch.setattr(preflight, "_check_nomic_present", lambda: (False, "missing"))
    monkeypatch.setattr(preflight, "_check_embed_endpoint", lambda: (False, "down"))
    monkeypatch.setattr(preflight.shutil, "which", lambda n: None)


def test_missing_services_warn_but_pass(monkeypatch, capsys):
    _no_services(monkeypatch)
    assert preflight.run_preflight() == 0
    out = capsys.readouterr().out
    assert "ollama-daemon" in out and "warning" in out.lower()


def test_core_failure_still_fails(monkeypatch):
    _no_services(monkeypatch)
    monkeypatch.setattr(preflight, "_check_sqlite_vec", lambda: (False, "no ext"))
    assert preflight.run_preflight() == 1
