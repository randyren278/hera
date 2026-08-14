"""CP-6: no SKILL.md contains a POSIX-only invocation construct.

Every hera-* SKILL.md must invoke engines through the OS-neutral convention
(``python <VAULT>/scripts/hera_cli.py <engine>``) — no bash-only preamble,
no venv-path hardcode, no ``bash install.sh``/``bash preflight.sh``.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = sorted((REPO / ".claude" / "skills").glob("hera-*/SKILL.md"))

# (label, compiled pattern) — a match anywhere in a SKILL.md is a failure.
BANNED = [
    ("dot-source of locator", re.compile(r'^\s*\.\s+"\$HOME', re.M)),
    ("dot-source of locator (vault)", re.compile(r'^\s*\.\s+"\$HERA', re.M)),
    ("bash ${VAR:?} guard", re.compile(r'\$\{[A-Z_]+:\?')),
    ("bash install.sh", re.compile(r'\bbash\s+install\.sh')),
    ("bash preflight.sh", re.compile(r'\bbash\s+.*preflight\.sh')),
    ("hardcoded .venv/bin/python", re.compile(r'\.venv/bin/python')),
    ("hardcoded .venv\\Scripts", re.compile(r'\.venv\\Scripts')),
    ("relative venv+scripts", re.compile(r'\.venv/bin/python\s+scripts/')),
]


def test_skills_exist():
    assert SKILLS, "no hera-* SKILL.md files found"
    assert len(SKILLS) == 5, f"expected 5 skills, found {len(SKILLS)}"


def test_no_posixisms_in_skills():
    failures = []
    for skill in SKILLS:
        text = skill.read_text(encoding="utf-8")
        for label, pat in BANNED:
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                failures.append(f"{skill.relative_to(REPO)}:{line}: {label} → {m.group(0)!r}")
    assert not failures, "POSIX-only constructs remain:\n" + "\n".join(failures)


def test_skills_name_hera_cli():
    """Every skill that invokes an engine names the OS-neutral launcher."""
    for skill in SKILLS:
        text = skill.read_text(encoding="utf-8")
        # hera-conflicts/prune/ingest/team run engines; setup runs hera_cli too.
        assert "hera_cli.py" in text, f"{skill.relative_to(REPO)} does not use hera_cli.py"
