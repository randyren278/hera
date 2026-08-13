"""ollama_provision.py — install the Ollama binary and start its daemon.

Companion to ``ollama.py``, which pulls the embedding model but deliberately
does *not* install the binary or start the daemon (its docstring calls that
"platform-specific and out of scope"). This module owns exactly that gap so a
cold-start ``install.py`` — Python present, nothing else — can bring Ollama up
end-to-end in one run.

Same conventions as ``ollama.py`` / ``preflight.py``:
  * stdlib only (``subprocess``, ``shutil``, ``urllib``, ``platform``, ``os``);
  * best-effort — **never raises**; every entry point returns ``(ok, detail)``
    so ``install.py``'s preflight stays the true pass/fail gate;
  * daemon reachability is reused from ``ollama._daemon_up()`` (single source).

``install_command()`` is the single source of truth for *how* Ollama installs
on this OS: it is rendered to a string for preflight's remedy line and executed
verbatim by ``install_binary()``.
"""
from __future__ import annotations

import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ollama  # scripts/install/ollama.py — reuse _daemon_up()
import ui  # scripts/install/ui.py — TTY-aware pretty-print

# Official silent-install download for the Windows no-winget fallback.
WINDOWS_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"


def ollama_on_path() -> bool:
    """True iff the ``ollama`` binary is on PATH."""
    return shutil.which("ollama") is not None


def install_command() -> list[str] | None:
    """The exact argv that installs Ollama on this OS, or ``None`` when no
    non-interactive path exists (caller should then only *guide*).

    Single source of truth: rendered to text by preflight's remedy line and
    executed verbatim by :func:`install_binary`. The Windows-no-winget case
    returns ``None`` here because that path is a download+run handled specially
    in :func:`install_binary`; :func:`install_command_text` still describes it.
    """
    system = platform.system()
    if system == "Windows":
        if shutil.which("winget"):
            return ["winget", "install", "--id", "Ollama.Ollama", "-e",
                    "--silent", "--accept-package-agreements",
                    "--accept-source-agreements"]
        return None  # download OllamaSetup.exe — see install_binary()
    if system == "Darwin":
        if shutil.which("brew"):
            return ["brew", "install", "ollama"]
        return None  # no clean silent CLI install without brew — guide only
    # Linux and other POSIX: the official one-liner.
    return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]


def install_command_text() -> str:
    """Human-facing remedy string for the current OS — always non-empty.

    Used by preflight's remedy line. Covers the cases where
    :func:`install_command` returns ``None`` (Windows-no-winget download,
    macOS-no-brew manual .dmg).
    """
    cmd = install_command()
    if cmd is not None:
        return " ".join(cmd)
    system = platform.system()
    if system == "Windows":
        return f"download and run {WINDOWS_SETUP_URL} (silent: /VERYSILENT)"
    if system == "Darwin":
        return "install Homebrew then `brew install ollama`, or download the .dmg from https://ollama.com/download"
    return "curl -fsSL https://ollama.com/install.sh | sh"


def _run(cmd: list[str]) -> tuple[bool, str]:
    """Run an install argv, streaming to the user's terminal. Never raises."""
    try:
        r = subprocess.run(cmd)
        return (r.returncode == 0), f"exit {r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def _install_windows_exe(dry: bool) -> tuple[bool, str]:
    """Windows fallback: download OllamaSetup.exe and run it silently."""
    if dry:
        ui.info(f"[dry] would download {WINDOWS_SETUP_URL} and run it /VERYSILENT")
        return True, "[dry] windows exe"
    try:
        tmp = pathlib.Path(tempfile.gettempdir()) / "OllamaSetup.exe"
        ui.info(f"downloading {WINDOWS_SETUP_URL} -> {tmp}")
        urllib.request.urlretrieve(WINDOWS_SETUP_URL, str(tmp))
    except Exception as e:  # network / filesystem — never fatal
        return False, f"download failed: {e}"
    return _run([str(tmp), "/VERYSILENT", "/NORESTART"])


def install_binary(dry: bool = False) -> tuple[bool, str]:
    """Install the Ollama binary for this OS. Best-effort; never raises.

    Returns ``(ok, detail)``. When no non-interactive path exists (macOS
    without Homebrew), returns ``(False, ...)`` with the manual remedy so the
    caller can surface it — install is not aborted, preflight is the gate.
    """
    if ollama_on_path():
        return True, "already installed"

    cmd = install_command()
    if cmd is None:
        # Windows-no-winget → download the .exe; anything else (macOS-no-brew)
        # has no silent path, so guide.
        if platform.system() == "Windows":
            return _install_windows_exe(dry)
        return False, "no non-interactive install path — " + install_command_text()

    if dry:
        ui.info(f"[dry] would install ollama: {' '.join(cmd)}")
        return True, "[dry] would install"

    ui.info(f"installing ollama: {' '.join(cmd)}")
    ok, detail = _run(cmd)
    return (ok, "installed" if ok else f"install failed ({detail})")


def start_daemon(dry: bool = False, wait_s: float = 10.0) -> tuple[bool, str]:
    """Start the Ollama daemon if it isn't already up. Best-effort; never raises.

    No-op when the daemon is already reachable. Otherwise spawns it detached
    (never blocks the installer) and polls ``ollama._daemon_up()`` up to
    ``wait_s`` seconds.
    """
    if ollama._daemon_up():
        return True, "daemon already running"

    if dry:
        ui.info("[dry] would start the ollama daemon")
        return True, "[dry] would start"

    exe = shutil.which("ollama")
    if not exe:
        return False, "ollama binary not on PATH — cannot start daemon"

    try:
        if platform.system() == "Windows":
            # The Windows installer normally auto-starts the background service;
            # launch the app as a belt-and-suspenders. DETACHED so we don't wait.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen([exe, "app"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags)
        else:
            # `ollama serve` in its own session, output discarded.
            subprocess.Popen([exe, "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not spawn daemon: {e}"

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if ollama._daemon_up():
            return True, "daemon started"
        time.sleep(0.5)
    return False, f"daemon did not come up within {wait_s:.0f}s"


def ensure_ollama(dry: bool = False, assume_yes: bool | None = None,
                  prompt_fn=None) -> tuple[bool, str]:
    """Bring Ollama up end-to-end: install the binary (if absent) and start the
    daemon (if down). Best-effort; **never raises**.

    Decision to install when the binary is absent:
      * ``assume_yes is True``  → install (``--install-ollama``);
      * ``assume_yes is False`` → skip    (``--no-install-ollama``);
      * ``assume_yes is None``  → ask via ``prompt_fn`` (default Yes) if given;
        with no ``prompt_fn`` (non-TTY), proceed with install.

    Returns ``(ok, detail)``. ``ok`` reflects daemon reachability at the end.
    """
    # Daemon already up — nothing to do (covers "already fully provisioned").
    if ollama._daemon_up():
        return True, "daemon already running"

    if ollama_on_path():
        # Installed but not running — just start it.
        return start_daemon(dry=dry)

    # Binary absent: decide whether to install.
    if assume_yes is False:
        return False, "ollama not installed and install declined — " + install_command_text()
    if assume_yes is None and prompt_fn is not None:
        if not prompt_fn():
            return False, "ollama not installed and install declined — " + install_command_text()
    # assume_yes is True, or None-with-no-prompt (non-TTY), or prompt said yes.

    ok, detail = install_binary(dry=dry)
    if not ok:
        return False, f"ollama install: {detail}"
    return start_daemon(dry=dry)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="assume yes (non-interactive)")
    a = ap.parse_args()
    ok, detail = ensure_ollama(dry=a.dry_run, assume_yes=True if a.yes else None)
    print(f"ensure_ollama: {'ok' if ok else 'FAIL'} ({detail})")
    raise SystemExit(0 if ok else 1)
