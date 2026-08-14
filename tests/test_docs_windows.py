"""CP-7: docs describe the Windows-capable, symlink-free, install.py model.

Guards against a regression that reintroduces the stale "symlink mirror" /
"bash install.sh" / "macOS or Linux only" story into README.md or
docs/GLOBAL_INSTALL.md.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")
GLOBAL = (REPO / "docs" / "GLOBAL_INSTALL.md").read_text(encoding="utf-8")


def test_readme_names_python_install():
    assert "python install.py" in README
    assert "bash install.sh" not in README


def test_readme_no_mac_linux_only_prereq():
    assert "macOS or Linux" not in README
    # The cross-platform prereq is present.
    assert re.search(r"Windows.*mac", README) or "Windows 10/11" in README


def test_global_install_describes_copy_not_symlink_mirror():
    # The stale claim was that hooks are symlinked into ~/.claude/hooks/.
    # That mirror must be gone from the described model.
    assert "symlinks pointing at" not in GLOBAL
    assert "Why symlinks, not copies" not in GLOBAL
    # The new model is stated affirmatively.
    assert "Why copies + in-repo hooks" in GLOBAL
    assert "no `~/.claude/hooks/` mirror" in GLOBAL or "no symlinks" in GLOBAL


def test_global_install_no_bash_source_hook_surface():
    # The old hook command surface sourced the locator with `. "$HOME/…"`.
    assert '. "$HOME/.claude/hera.env" &&' not in GLOBAL
    # Locator is plain KEY=VALUE, not `export`.
    assert "export HERA_VAULT=" not in GLOBAL


def test_global_install_has_windows_notes():
    assert "Windows notes" in GLOBAL
    assert "Developer Mode is not required" in GLOBAL
    assert r".venv\Scripts\python.exe" in GLOBAL


def test_global_install_references_python_engines_not_shell_lib():
    # Engine references should be the ported Python modules, not lib.sh/locator.sh.
    assert "scripts/install/lib.sh" not in GLOBAL
    assert "scripts/install/locator.sh" not in GLOBAL
    assert "scripts/install/settings.py" in GLOBAL
    assert "scripts/install/locator.py" in GLOBAL
