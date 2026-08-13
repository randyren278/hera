"""CP-4: ingest/publish resolve the claude binary and degrade gracefully.

- CLAUDE_BIN prefers $CLAUDE_BIN, then shutil.which("claude"), then "claude".
- A missing binary raises FileNotFoundError from subprocess; the two soft call
  sites swallow it (return None / (False, "")) and the hard site (extract)
  re-raises a clear RuntimeError — never an uncaught FileNotFoundError.
"""
from __future__ import annotations

import importlib
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import ingest
    return importlib.reload(ingest)


def test_env_var_wins(monkeypatch):
    ingest = _reload(monkeypatch, CLAUDE_BIN="/custom/claude")
    assert ingest.CLAUDE_BIN == "/custom/claude"


def test_falls_back_to_which(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/found/on/path/claude")
    ingest = _reload(monkeypatch, CLAUDE_BIN=None)
    assert ingest.CLAUDE_BIN == "/found/on/path/claude"


def test_falls_back_to_literal_claude(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ingest = _reload(monkeypatch, CLAUDE_BIN=None)
    assert ingest.CLAUDE_BIN == "claude"


def test_soft_sites_swallow_missing_binary(monkeypatch):
    """When claude is absent, contradiction + explicit-statement checks return
    a safe default rather than raising."""
    ingest = _reload(monkeypatch, CLAUDE_BIN="/nonexistent/claude-binary-xyz")
    # _detect_contradiction → None on FileNotFoundError.
    assert ingest._detect_contradiction("old body", "new body") is None
    # _user_stated_explicitly → (False, "") on FileNotFoundError.
    ok, _ = ingest._user_stated_explicitly("transcript", "claim")
    assert ok is False


def test_hard_site_reraises_clear_error(monkeypatch):
    """The primary extractor raises a clear RuntimeError (not a bare
    FileNotFoundError) when claude is missing."""
    import pytest
    ingest = _reload(monkeypatch, CLAUDE_BIN="/nonexistent/claude-binary-xyz")
    with pytest.raises(RuntimeError, match="claude binary not found"):
        ingest._call_claude_extract("some raw text")


def test_publish_bin_resolution(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/p/claude")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    import publish
    publish = importlib.reload(publish)
    assert publish.CLAUDE_BIN == "/p/claude"


def test_teardown_reload(monkeypatch):
    """Restore ingest/publish to their unmonkeypatched module state so later
    tests import the real CLAUDE_BIN."""
    import ingest, publish
    importlib.reload(ingest)
    importlib.reload(publish)


def test_bin_resolution_survives_isolation_flags(monkeypatch):
    """CLAUDE_BIN resolution is independent of the isolation flags; a change
    to one must not silently break the other."""
    ingest = _reload(monkeypatch, CLAUDE_BIN="/custom/claude")
    assert ingest.CLAUDE_BIN == "/custom/claude"
    assert "--bare" not in ingest.CLAUDE_ISOLATION
    importlib.reload(ingest)
