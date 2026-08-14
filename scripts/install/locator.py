"""locator.py — write the vault-pointer file that global hooks/skills read.

Pure-Python port of locator.sh. Writes ``HERA_VAULT=<abs path>`` to the
locator file (default ``~/.claude/hera.env``) as plain ``KEY=VALUE`` with
**no** ``export`` prefix — the file is read by Python (``os.environ``-style parse),
not sourced by a shell, so it must be OS-neutral.

Idempotent: the ``HERA_VAULT`` line (and our header comments) are rewritten
on every run; any other line — e.g. ``HERA_TEAM_REMOTE`` added by
/hera-setup — is preserved verbatim. Atomic write via tmp + ``os.replace``.
"""
from __future__ import annotations

import os
import pathlib
import re

HEADER = [
    "# Hera vault locator — written by install.py",
    "# Read by global hooks in ~/.claude/hooks/ and by the hera-* skills.",
]

# Lines this module owns and rewrites on every run. Everything else is carried
# through. Matches our header comments and the HERA_VAULT assignment in
# both the new (no-export) and legacy (export) forms so a re-install over an old
# locator.sh-written file cleans up the stale `export`.
_MANAGED_RE = re.compile(
    r"^\s*(?:export\s+)?HERA_VAULT\s*=|"
    r"^\s*#\s*Hera vault locator|"
    r"^\s*#\s*Read by global hooks"
)


def default_target() -> pathlib.Path:
    """The locator path, honoring HERA_LOC_TARGET for tests/overrides."""
    override = os.environ.get("HERA_LOC_TARGET")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".claude" / "hera.env"


def parse_locator(path: pathlib.Path) -> dict[str, str]:
    """Parse a KEY=VALUE locator file into a dict. Ignores comments/blank lines.
    Tolerates a legacy ``export KEY=...`` prefix and surrounding quotes."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_locator(vault: pathlib.Path, target: pathlib.Path | None = None) -> pathlib.Path:
    """Write ``HERA_VAULT=<abs>`` into ``target``, preserving other lines.

    Returns the target path. Raises NotADirectoryError if ``vault`` isn't a dir.
    """
    vault = pathlib.Path(vault)
    if not vault.is_dir():
        raise NotADirectoryError(f"not a directory: {vault}")
    abs_vault = pathlib.Path(os.path.realpath(vault))

    target = target or default_target()
    target.parent.mkdir(parents=True, exist_ok=True)

    preserved: list[str] = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not _MANAGED_RE.match(line):
                preserved.append(line)

    lines = list(HEADER)
    lines.append(f'HERA_VAULT="{abs_vault}"')
    # Drop leading blank lines that would otherwise accumulate before preserved
    # content, but keep interior structure intact.
    lines.extend(preserved)
    body = "\n".join(lines).rstrip("\n") + "\n"

    tmp = target.with_name(target.name + f".tmp.{os.getpid()}")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return target


def remove_locator(target: pathlib.Path | None = None) -> bool:
    """Delete the locator file. Returns True if a file was removed."""
    target = target or default_target()
    if target.exists():
        target.unlink()
        return True
    return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        sys.stderr.write("locator.py: usage: locator.py <vault-dir>\n")
        sys.exit(2)
    try:
        out = write_locator(pathlib.Path(sys.argv[1]))
    except NotADirectoryError as e:
        sys.stderr.write(f"locator.py: {e}\n")
        sys.exit(1)
    print(out)
