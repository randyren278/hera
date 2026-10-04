"""Shared fixtures for the Python install/uninstall test suite.

Builds an isolated *vault copy* under tmp so tests exercise install.py's real
orchestration without touching the live repo's project settings or ~/.claude.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# The minimal surface install.py touches, copied into the isolated vault.
_VAULT_COPY = [
    "install.py",
    "scripts/install",
    "scripts/codex_hook.py",
    "scripts/hera_db.py",
    "scripts/locks.py",
    "scripts/embed.py",
    "scripts/search.py",
    "scripts/ingest.py",
    "scripts/publish.py",
    "scripts/conflicts.py",
    ".claude/skills",
    ".claude/hooks",
    "CLAUDE.md",
    "AGENTS.md",
]


def pytest_addoption(parser):
    parser.addoption("--live-llm", action="store_true", default=False,
                     help="run tests that make real Claude subscription calls")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_ollama: needs a live embedding endpoint (Ollama + "
        "nomic-embed-text); skipped when it is down unless HERA_REQUIRE_OLLAMA=1")


def _embed_up() -> bool:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import embed
        embed.embed("probe")
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    wanted = [i for i in items if i.get_closest_marker("needs_ollama")]
    # CI sets HERA_REQUIRE_OLLAMA=1 so an embedder outage fails loudly
    # instead of silently skipping the retrieval/trust suite.
    if not wanted or os.environ.get("HERA_REQUIRE_OLLAMA") == "1" or _embed_up():
        return
    skip = pytest.mark.skip(reason="embedding endpoint (Ollama) down")
    for item in wanted:
        item.add_marker(skip)


def _copy_into(dst_vault: pathlib.Path) -> None:
    for rel in _VAULT_COPY:
        src = REPO / rel
        target = dst_vault / rel
        if not src.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)


@pytest.fixture
def vault_env(tmp_path):
    """An isolated vault + fake CLAUDE_HOME. Yields dict(vault, home, run)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _copy_into(vault)
    # Symlink the real .venv so install.py's venv step is a no-op (interpreter
    # already present) — we test registration/settings/uninstall, not bootstrap.
    (vault / ".venv").symlink_to(REPO / ".venv")
    # A pre-existing project settings.json to exercise disable/re-enable.
    (vault / ".claude").mkdir(parents=True, exist_ok=True)
    proj = vault / ".claude" / "settings.json"
    proj.write_text('{"hooks":{}}\n', encoding="utf-8")

    home = tmp_path / "claude_home"
    codex_home = tmp_path / "codex_home"

    def run(*args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CLAUDE_HOME"] = str(home)
        env["CODEX_HOME"] = str(codex_home)
        # Skip the real preflight/Ollama by pre-creating hera.db marker: the
        # venv step sees .venv present (symlink), and we pass --dry-run off but
        # the fixture vault has no preflight.py yet at CP-3 time → install.py
        # prints "(preflight.py not present yet)" and continues.
        return subprocess.run(
            [sys.executable, str(vault / "install.py"), *args],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
            cwd=str(vault),
        )

    yield {"vault": vault, "home": home, "codex_home": codex_home, "run": run, "proj": proj}


def count_symlinks(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*") if p.is_symlink()] if root.exists() else []
