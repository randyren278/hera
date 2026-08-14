"""Nested `claude -p` calls must be isolated WITHOUT --bare.

--bare skips keychain reads, so a subscription (OAuth) login is invisible to
the subprocess and every nested call fails with "Not logged in". The four
isolation flags below achieve the same recursion-safety --bare gave us --
in fact a stronger one, since --bare never isolated MCP servers or tools --
while leaving auth alone.
"""
from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

REQUIRED_FLAGS = [
    ("--setting-sources", ""),
    ("--tools", ""),
]
REQUIRED_BARE_FLAGS = ["--strict-mcp-config", "--disable-slash-commands"]


@pytest.fixture
def modules():
    import ingest
    import publish
    return importlib.reload(ingest), importlib.reload(publish)


def _capture(monkeypatch, module, fn, *args):
    """Run `fn` with subprocess.run stubbed; return (cmd, kwargs)."""
    seen = {}

    class _R:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _R()

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    fn(*args)
    return seen["cmd"], seen["kwargs"]


def test_model_defaults_to_sonnet(modules, monkeypatch):
    """claude-opus-latest no longer resolves; sonnet is the default."""
    ingest, publish = modules
    assert ingest.CLAUDE_MODEL == "sonnet"
    assert publish.CLAUDE_MODEL == "sonnet"


def test_model_honours_env_override(monkeypatch):
    monkeypatch.setenv("HERA_CLAUDE_MODEL", "opus")
    import ingest
    ingest = importlib.reload(ingest)
    assert ingest.CLAUDE_MODEL == "opus"
    monkeypatch.delenv("HERA_CLAUDE_MODEL")
    importlib.reload(ingest)


def test_isolation_constant_shape(modules):
    ingest, publish = modules
    for mod in (ingest, publish):
        iso = mod.CLAUDE_ISOLATION
        assert "--bare" not in iso
        for flag, value in REQUIRED_FLAGS:
            assert flag in iso, f"{mod.__name__}: missing {flag}"
            assert iso[iso.index(flag) + 1] == value, \
                f"{mod.__name__}: {flag} must be followed by an empty string"
        for flag in REQUIRED_BARE_FLAGS:
            assert flag in iso, f"{mod.__name__}: missing {flag}"


@pytest.mark.parametrize("site", ["explicit", "contradiction", "extract", "strip"])
def test_call_site_is_isolated(modules, monkeypatch, site):
    ingest, publish = modules
    if site == "explicit":
        cmd, kwargs = _capture(monkeypatch, ingest,
                               ingest._user_stated_explicitly, "transcript", "claim")
    elif site == "contradiction":
        cmd, kwargs = _capture(monkeypatch, ingest,
                               ingest._detect_contradiction, "old", "new")
    elif site == "extract":
        cmd, kwargs = _capture(monkeypatch, ingest,
                               ingest._call_claude_extract, "raw text")
    else:
        cmd, kwargs = _capture(monkeypatch, publish, publish._strip_body, "body")

    assert "--bare" not in cmd, f"{site}: --bare breaks subscription auth"
    for flag, value in REQUIRED_FLAGS:
        assert flag in cmd, f"{site}: missing {flag}"
        assert cmd[cmd.index(flag) + 1] == value
    for flag in REQUIRED_BARE_FLAGS:
        assert flag in cmd, f"{site}: missing {flag}"

    cwd = kwargs.get("cwd")
    assert cwd is not None, f"{site}: must set cwd outside the vault"
    assert REPO not in pathlib.Path(cwd).resolve().parents, \
        f"{site}: cwd {cwd} is inside the vault; CLAUDE.md would be auto-discovered"
    assert pathlib.Path(cwd).resolve() != REPO


@pytest.mark.skipif(
    "not config.getoption('--live-llm', default=False)",
    reason="needs a real Claude subscription; pass --live-llm to run",
)
def test_live_nested_call_authenticates(modules):
    """The regression this whole task exists for: a real nested call must not
    fail with 'Not logged in'."""
    ingest, _ = modules
    out = ingest._detect_contradiction(
        "The sky is blue.", "The sky is green.")
    assert out is not None, "nested claude -p returned nothing (auth failure?)"
    assert out.get("verdict") in {"contradiction", "no_contradiction", "elaboration"}
