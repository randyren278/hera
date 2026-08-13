"""CP-4: locks.py liveness never calls a destructive os.kill on Windows."""
from __future__ import annotations

import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import locks  # noqa: E402


def test_posix_dead_pid_is_not_alive():
    # A very high pid is almost certainly not a running process.
    assert locks._pid_alive(2_000_000_000) is False


def test_posix_self_is_alive():
    assert locks._pid_alive(os.getpid()) is True


def test_zero_or_negative_pid_not_alive():
    assert locks._pid_alive(0) is False
    assert locks._pid_alive(-1) is False


def test_windows_liveness_never_calls_os_kill(monkeypatch):
    """On Windows, os.kill(pid, sig) terminates the process — the liveness
    probe must NEVER call it. Force the nt branch and assert os.kill is untouched."""
    monkeypatch.setattr(os, "name", "nt")

    called = {"os_kill": False}
    real_kill = os.kill

    def guard(*a, **k):
        called["os_kill"] = True
        return real_kill(*a, **k)

    monkeypatch.setattr(os, "kill", guard)

    # Route the windows probe to a deterministic stub so the test is host-agnostic
    # (no ctypes.windll on non-Windows CI).
    monkeypatch.setattr(locks, "_pid_alive_windows", lambda pid: True)

    assert locks._pid_alive(1234) is True
    assert called["os_kill"] is False, "os.kill was called on the Windows path"


def test_windows_probe_treats_uncertainty_as_alive(monkeypatch):
    """The tasklist fallback must default to 'alive' on any error so we never
    break a live lock we can't inspect."""
    monkeypatch.setattr(os, "name", "nt")

    # Force the ctypes path to raise and the subprocess fallback to raise too.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "ctypes":
            raise ImportError("no ctypes on this host (simulated)")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    import subprocess as sp
    def boom(*a, **k):
        raise OSError("tasklist unavailable (simulated)")
    monkeypatch.setattr(sp, "run", boom)

    assert locks._pid_alive_windows(4321) is True
