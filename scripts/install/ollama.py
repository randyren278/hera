"""ollama.py — provision the embedding model the vault needs.

Companion to preflight.py, which only *verifies* that ``nomic-embed-text`` is
pulled and serves 768-dim embeddings. Verification alone left a fresh install
failing: Ollama ships without models, so a clean machine has nothing to serve
until someone runs ``ollama pull``. This module fulfills the ``hera-setup``
SKILL.md step-4 promise ("pull nomic-embed-text if missing") from the install
path, *before* preflight verifies.

Same conventions as preflight.py: stdlib ``urllib``/``shutil`` only, all Ollama
traffic over HTTP at ``OLLAMA_BASE``. HTTP-first (not ``ollama pull``) so the
daemon-reachable-but-CLI-absent case — real on Windows — still works; the CLI is
only a fallback.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"


def _daemon_up(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_BASE + "/api/version", timeout=timeout):
            return True
    except Exception:
        return False


def _model_present(model: str, timeout: float = 2.0) -> bool:
    # Mirror preflight._check_nomic_present: substring match over /api/tags so a
    # tag like "nomic-embed-text:latest" still counts.
    try:
        with urllib.request.urlopen(OLLAMA_BASE + "/api/tags", timeout=timeout) as r:
            tags = json.loads(r.read().decode("utf-8"))
    except Exception:
        return False
    names = [m.get("name", "") for m in tags.get("models", [])]
    return any(model in n for n in names)


def _pull_http(model: str, timeout: float = 600.0) -> tuple[bool, str]:
    """Pull via POST /api/pull, consuming the NDJSON progress stream.

    Returns once the daemon reports ``status: success`` (or the stream ends).
    Works without the ``ollama`` binary on PATH.
    """
    data = json.dumps({"name": model}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_BASE + "/api/pull", data=data,
                                 headers={"Content-Type": "application/json"})
    last = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if "error" in obj:
                    return False, obj["error"]
                status = obj.get("status", "")
                if status and status != last:
                    print(f"    ollama: {status}")
                    last = status
                if status == "success":
                    return True, "pulled"
    except Exception as e:
        return False, str(e)
    # Stream ended without an explicit error — trust a post-pull presence check.
    return _model_present(model), "pulled" if _model_present(model) else "pull stream ended without success"


def _pull_cli(model: str) -> tuple[bool, str]:
    exe = shutil.which("ollama")
    if not exe:
        return False, "ollama binary not on PATH"
    r = subprocess.run([exe, "pull", model], text=True)
    return (r.returncode == 0), "pulled" if r.returncode == 0 else f"ollama pull exited {r.returncode}"


def ensure_model(model: str = DEFAULT_MODEL, dry: bool = False) -> tuple[bool, str]:
    """Ensure ``model`` is available in the local Ollama daemon.

    Best-effort provisioning — never raises. Returns ``(ok, detail)``:
    - daemon unreachable → ``(False, ...)`` (do not try to start it; that's
      platform-specific and out of scope). preflight will FAIL clearly.
    - already present    → ``(True, "already present")``.
    - pulled (HTTP, CLI fallback) → ``(True, "pulled")``; else ``(False, err)``.
    With ``dry`` no download happens; the intended action is printed instead.
    """
    up = _daemon_up()
    if dry:
        # Show the plan in every daemon state; never probe-and-fail or mutate.
        if not up:
            print(f"  [dry] ollama daemon down — would pull {model!r} once it's up")
            return True, "[dry] daemon down"
        if _model_present(model):
            print(f"  [dry] ollama model {model!r} already present — nothing to pull")
            return True, "[dry] already present"
        print(f"  [dry] would pull ollama model {model!r}")
        return True, "[dry] would pull"

    if not up:
        return False, "ollama daemon unreachable at " + OLLAMA_BASE + " — start it, then re-run"
    if _model_present(model):
        return True, "already present"

    print(f"  pulling ollama model {model!r} (first run only; this can take a few minutes)…")
    ok, detail = _pull_http(model)
    if not ok:
        # HTTP path failed (e.g. daemon variant without /api/pull) — try the CLI.
        cli_ok, cli_detail = _pull_cli(model)
        if cli_ok:
            return True, "pulled"
        return False, f"pull failed (http: {detail}; cli: {cli_detail})"
    return True, detail


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ok, detail = ensure_model(a.model, dry=a.dry_run)
    print(f"ensure_model: {'ok' if ok else 'FAIL'} ({detail})")
    raise SystemExit(0 if ok else 1)
