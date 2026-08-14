"""CP-1: locator.py writes a shell-neutral KEY=VALUE file with no `export`."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "install"))
import locator  # noqa: E402


def test_no_export_prefix(tmp_path):
    target = tmp_path / "hera.env"
    locator.write_locator(REPO, target)
    text = target.read_text(encoding="utf-8")
    assert "HERA_VAULT=" in text
    # The core Windows requirement: no shell `export`, which cmd.exe can't run.
    for line in text.splitlines():
        assert not line.lstrip().startswith("export "), f"found export line: {line!r}"


def test_value_is_absolute_realpath(tmp_path):
    target = tmp_path / "loc.env"
    locator.write_locator(REPO, target)
    parsed = locator.parse_locator(target)
    got = pathlib.Path(parsed["HERA_VAULT"])
    assert got.is_absolute()
    assert got == pathlib.Path(__import__("os").path.realpath(REPO))


def test_preserves_other_lines(tmp_path):
    target = tmp_path / "loc.env"
    target.write_text(
        "# Hera vault locator — written by install.py\n"
        'export HERA_VAULT="/old/stale/path"\n'
        'HERA_TEAM_REMOTE="git@example.com:team/hera.git"\n',
        encoding="utf-8",
    )
    locator.write_locator(REPO, target)
    parsed = locator.parse_locator(target)
    # Our line rewritten to the new vault, no stale export duplicated.
    assert parsed["HERA_VAULT"] == str(pathlib.Path(__import__("os").path.realpath(REPO)))
    # Non-managed line survives.
    assert parsed["HERA_TEAM_REMOTE"] == "git@example.com:team/hera.git"
    # And it appears exactly once.
    text = target.read_text(encoding="utf-8")
    assert text.count("HERA_VAULT=") == 1
    assert text.count("HERA_TEAM_REMOTE=") == 1


def test_idempotent_rewrite(tmp_path):
    target = tmp_path / "loc.env"
    locator.write_locator(REPO, target)
    first = target.read_text(encoding="utf-8")
    locator.write_locator(REPO, target)
    assert target.read_text(encoding="utf-8") == first


def test_remove_locator(tmp_path):
    target = tmp_path / "loc.env"
    locator.write_locator(REPO, target)
    assert locator.remove_locator(target) is True
    assert not target.exists()
    assert locator.remove_locator(target) is False


def test_not_a_directory_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        locator.write_locator(tmp_path / "does-not-exist", tmp_path / "loc.env")
