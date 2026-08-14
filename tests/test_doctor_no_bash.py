"""CP-5: hera_db --doctor never depends on bash for its preflight step."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "install"))
import hera_db  # noqa: E402
import preflight  # noqa: E402


def test_preflight_module_importable_and_callable():
    # The doctor prefers this in-process; it must exist and be callable.
    assert hasattr(preflight, "run_preflight")


def test_doctor_uses_python_preflight_not_bash(monkeypatch, capsys):
    """With bash hidden AND the python preflight forced green, doctor's
    preflight step must succeed via Python and never shell out to bash."""
    # Hide bash entirely.
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "bash" else real_which(n))

    # Force the python preflight to a deterministic green so this test doesn't
    # depend on Ollama being up on the CI host.
    monkeypatch.setattr(preflight, "run_preflight", lambda vault=None, verbose=False: 0)

    # Guard: if doctor ever tries to run `bash preflight.sh`, fail loudly.
    real_run = subprocess.run

    def guard_run(cmd, *a, **k):
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) else cmd
        assert "bash" not in str(argv0), f"doctor shelled out to bash: {cmd}"
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guard_run)

    rc = hera_db.doctor(verbose=False)
    out = capsys.readouterr().out
    assert "ok    preflight" in out
    # doctor's own return is about DB integrity etc.; on this healthy repo it's 0.
    assert rc == 0, out


def test_doctor_warns_when_no_python_preflight_and_no_bash(monkeypatch, capsys):
    """If the python preflight module is absent AND bash is unavailable, doctor
    must WARN (not hard-fail) about preflight — never crash."""
    # Simulate preflight.py missing by making its import raise ModuleNotFoundError.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "preflight":
            raise ModuleNotFoundError("simulated: no preflight module")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "bash" else real_which(n))

    rc = hera_db.doctor(verbose=False)
    out = capsys.readouterr().out
    # Preflight is skipped-with-warning, not a FAIL.
    assert "preflight" in out
    assert "FAIL  preflight" not in out
