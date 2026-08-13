#!/usr/bin/env python3
"""probe_hook_paths.py — verify each hook resolves REPO to $SECOND_BRAIN_VAULT.

Imports each hook module, reads its module-level REPO attribute, and
asserts it matches the current SECOND_BRAIN_VAULT env var (or the vault
implied by this script's own location if the env var is unset).

Exit 0 on all-good; exit 1 on the first mismatch.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys


HOOKS = ["session_start", "prompt_inject", "stop_score", "session_end_file"]


def load(hook_name: str, hook_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"probe_{hook_name}", hook_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load spec for {hook_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main() -> int:
    # If invoked without $SECOND_BRAIN_VAULT, treat the repo containing this
    # script as the expected vault — that's the project-local case.
    env_vault = os.environ.get("SECOND_BRAIN_VAULT")
    self_repo = pathlib.Path(__file__).resolve().parents[2]  # scripts/install/../.. == repo
    expected = pathlib.Path(env_vault).resolve() if env_vault else self_repo

    hooks_dir = expected / ".claude" / "hooks"
    fail = False
    for name in HOOKS:
        path = hooks_dir / f"{name}.py"
        if not path.exists():
            print(f"probe: missing hook {path}", file=sys.stderr)
            fail = True
            continue
        mod = load(name, path)
        got = getattr(mod, "REPO", None)
        if got is None:
            print(f"probe: {name}: no REPO attribute", file=sys.stderr)
            fail = True
            continue
        if pathlib.Path(got).resolve() != expected:
            print(f"probe: {name}: REPO={got} != expected {expected}", file=sys.stderr)
            fail = True
        else:
            print(f"  ok    {name}: REPO={got}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
