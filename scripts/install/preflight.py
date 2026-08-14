"""preflight.py — OS-neutral environment checks (port of preflight.sh).

Callable in-process from install.py (step 1) and hera_db.py --doctor, so
neither needs ``bash``/``curl``/``grep``/the standalone ``sqlite3`` CLI. Each
check uses the Python stdlib (``sqlite3``, ``urllib``) or an import probe.

``run_preflight(vault)`` returns 0 iff every check passes, else 1 — same
semantics as the shell version. It runs against the *current* interpreter,
which install.py/hera_db.py invoke via the vault's ``.venv`` python, so the
library-import checks reflect the venv, not the system python.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ui  # scripts/install/ui.py — TTY-aware pretty-print layer

OLLAMA_BASE = "http://localhost:11434"


def _emit(name: str, ok: bool, verbose: bool, detail: str = "") -> bool:
    text = name
    if detail and (verbose or not ok):
        text += f"  ({detail})"
    if ok:
        ui.ok(text)
    else:
        ui.fail(text)
    return ok


def _check_python() -> tuple[bool, str]:
    return sys.version_info >= (3, 8), f"{sys.version_info.major}.{sys.version_info.minor}"


def _check_import(mod: str) -> tuple[bool, str]:
    try:
        __import__(mod)
        return True, ""
    except Exception as e:  # ImportError or a load-time error
        return False, str(e)


def _check_ext_loading() -> tuple[bool, str]:
    ok = hasattr(sqlite3.Connection, "enable_load_extension")
    return ok, "" if ok else "this python lacks sqlite3 extension loading"


def _check_fts5() -> tuple[bool, str]:
    # Use the sqlite3 module directly — no standalone `sqlite3` CLI dependency.
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True, ""
    except Exception as e:
        return False, str(e)


def _check_sqlite_vec() -> tuple[bool, str]:
    try:
        import sqlite_vec
        c = sqlite3.connect(":memory:")
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        row = c.execute("SELECT vec_version()").fetchone()
        c.close()
        return bool(row), "" if row else "vec_version() returned nothing"
    except Exception as e:
        return False, str(e)


def _http_get(path: str, timeout: float = 2.0) -> bytes | None:
    try:
        with urllib.request.urlopen(OLLAMA_BASE + path, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _http_post_json(path: str, payload: dict, timeout: float = 15.0) -> dict | None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(OLLAMA_BASE + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _check_ollama_daemon() -> tuple[bool, str]:
    body = _http_get("/api/version")
    return (body is not None), "" if body else "daemon unreachable at " + OLLAMA_BASE


def _check_nomic_present() -> tuple[bool, str]:
    # Prefer the API (/api/tags) over shelling out to `ollama list`.
    body = _http_get("/api/tags")
    if body is None:
        return False, "cannot list models (daemon unreachable)"
    try:
        tags = json.loads(body.decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        ok = any("nomic-embed-text" in n for n in names)
        return ok, "" if ok else "nomic-embed-text not pulled"
    except Exception as e:
        return False, str(e)


def _check_embed_endpoint() -> tuple[bool, str]:
    resp = _http_post_json("/api/embeddings",
                           {"model": "nomic-embed-text", "prompt": "hi"})
    if resp is None:
        return False, "embeddings endpoint did not respond"
    emb = resp.get("embedding", [])
    return (len(emb) == 768), f"got {len(emb)} dims (want 768)"


def _emit_ollama_remedies(status: dict[str, bool]) -> None:
    """Print an actionable fix for each failed Ollama check. Best-effort — a
    missing/broken ``ollama_provision`` import must never crash preflight."""
    if status.get("ollama-binary") is False:
        try:
            import ollama_provision
            cmd = ollama_provision.install_command_text()
        except Exception:
            cmd = "see https://ollama.com/download"
        ui.warn(f"ollama not installed — install it with: {cmd}")
        ui.warn("  or re-run install.py, which can install it for you.")
    elif status.get("ollama-daemon") is False:
        # Binary present but daemon down.
        ui.warn("ollama installed but not running — start it (`ollama serve` "
                "or launch the app), or re-run install.py.")
    elif status.get("nomic-embed-text") is False:
        ui.warn("embedding model missing — pull it with: "
                "ollama pull nomic-embed-text (install.py / hera-setup do this "
                "automatically).")


def run_preflight(vault: pathlib.Path | None = None, verbose: bool = False) -> int:
    """Run every environment check. Return 0 iff all pass, else 1."""
    ui.step("preflight:")
    ui.plain(f"  interpreter: {sys.executable}")

    results: list[bool] = []
    status: dict[str, bool] = {}

    def run(name, fn):
        ok, detail = fn()
        status[name] = ok
        results.append(_emit(name, ok, verbose, detail))

    run("python>=3.8", _check_python)
    run("pyyaml", lambda: _check_import("yaml"))
    run("sqlite-ext-loading", _check_ext_loading)
    run("sqlite-fts5", _check_fts5)
    run("sqlite-vec-loads", _check_sqlite_vec)
    run("ulid-py", lambda: _check_import("ulid"))
    run("requests", lambda: _check_import("requests"))
    run("pytest", lambda: _check_import("pytest"))
    run("ollama-binary", lambda: (shutil.which("ollama") is not None, "not on PATH"))
    run("ollama-daemon", _check_ollama_daemon)
    run("nomic-embed-text", _check_nomic_present)
    run("embed-endpoint", _check_embed_endpoint)
    run("claude-code", lambda: (shutil.which("claude") is not None, "not on PATH"))

    _emit_ollama_remedies(status)

    if all(results):
        ui.plain("preflight: ok")
        return 0
    ui.plain("preflight: FAIL")
    return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    sys.exit(run_preflight(verbose=a.verbose))
