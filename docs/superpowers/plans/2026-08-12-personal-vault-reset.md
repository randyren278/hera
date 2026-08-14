# Hera Personal Vault Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port this second-brain vault from a work machine to a personal laptop — fix nested `claude -p` so it authenticates against an Anthropic subscription, remove the SAP seed pack, strip corporate identifiers, rebuild the stale environment, and get the full test suite green.

**Architecture:** Five sequential workstreams. The environment rebuild lands first because a `.venv` copied from the old machine (missing `sqlite_vec/vec0.dylib`) causes all 17 test errors and, via the install preflight check, all 10 install-test failures — nothing else is verifiable until it is fixed. The LLM fix replaces `--bare` (which skips keychain reads and therefore cannot see subscription OAuth credentials) with explicit isolation flags that achieve the same recursion-safety without touching auth. Seed, docs, and data-wipe work is independent and follows.

**Tech Stack:** Python 3.14 (`.venv`), SQLite + `sqlite-vec` + FTS5, Ollama (`nomic-embed-text`) for embeddings, Claude Code CLI as a subprocess for extraction/contradiction/redaction, pytest + bash e2e scripts.

**Spec:** `docs/superpowers/specs/2026-08-12-personal-vault-reset-design.md`

## Global Constraints

- **Never invoke `claude -p` with `--bare`.** `--bare` skips keychain reads; subscription OAuth auth then fails with `Not logged in`. This replaces the old NEVER-5 invariant, which mandated the opposite.
- **Every nested `claude -p` call must pass all four isolation flags** — `--setting-sources ""`, `--strict-mcp-config`, `--tools ""`, `--disable-slash-commands` — and must set `cwd` to a directory outside the vault.
- `HERA_CLAUDE_MODEL` defaults to **`sonnet`**. `claude-opus-latest` no longer resolves.
- **All writes to `wiki/` go through `locks.lock()`.** Never bypass.
- **Never let team content into personal `hera.db`** (ADR-14). `team_index.py` opens `TEAM_DB`; personal engines open `hera.db`.
- **Never delete from `wiki/.archive/` in engine code.** (The one-time wipe in Task 9 is an explicit, human-approved exception.)
- **The team-brain subsystem is out of scope.** No files removed, no ADR-14 change. It stays for collaborating with friends.
- Project name is **Hera**. `HERA_OWNER` stays `randy`. Do **not** rename `HERA_VAULT`, `HERA_OFF`, or `~/.claude/hera.env`.
- Audit regex requires word boundaries: `\b(sap|fpa106|harca|qrc|i771473|wdf|successfactors)\b|magnum opus|s-4hana`. Without `\b`, `sap` matches `disappeared`.
- Run Python via `.venv/bin/python` from the repo root. Commit after every task.

---

## File Structure

**Created:**
- `tests/test_nested_claude_isolation.py` — asserts all four call sites pass the isolation flags, no `--bare`, correct `cwd`, and the `sonnet` default.
- `tests/fixtures/seed-pack/concepts/*.md`, `tests/fixtures/seed-pack/entities/*.md` — a 4-page neutral pack with baked ULIDs, replacing `seed/fpa106` as the e2e fixture.

**Modified:**
- `scripts/ingest.py` — `CLAUDE_MODEL` default, new `CLAUDE_ISOLATION` constant, 3 call sites, `seed_wins` → `pinned_wins`.
- `scripts/publish.py` — re-exports `CLAUDE_MODEL`/`CLAUDE_ISOLATION`/`CLAUDE_CWD` from `ingest` (already imported at line 31), 1 call site.
- `tests/test_claude_bin_resolve.py` — command-shape assertions.
- `scripts/e2e_seed_index.sh`, `scripts/e2e_seed_guards.sh` — repointed at the fixture pack.
- `scripts/seed_index.py`, `.claude/skills/hera-setup/SKILL.md`, `seed/README.md` — de-seeded.
- `README.md`, `docs/README.md`, `docs/DECISIONS.md`, `docs/GLOBAL_INSTALL.md`, `docs/PIPELINES.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`, `.claude/skills/hera-ingest/SKILL.md`, `tests/test_hookcmd.py`, `.claude/settings.local.json`.

**Deleted:**
- `seed/fpa106/` (137 pages), `scripts/build_seed_pack.py`.

**Task dependency order:** 1 → 2 → (3,4,5 independent) → 6 → 7 → 8 → 9 → 10.

---

### Task 1: Rebuild the environment

Nothing below is verifiable until this lands. The copied `.venv` has `pyvenv.cfg` pointing at `/Users/I771473/Desktop/magnum opus/.venv` and is missing `sqlite_vec/vec0.dylib`.

**Files:**
- Delete: `.venv/`, all `__pycache__/`, `.pytest_cache/`, `.checkpoints/state.json`
- Reference: `scripts/install/venv.py:20` (`PIP_DEPS`), `scripts/install/venv.py:69` (`ensure_venv`)

**Interfaces:**
- Produces: a working `.venv/bin/python` where `import sqlite_vec; sqlite_vec.load(conn)` succeeds. Every later task depends on this.

- [ ] **Step 1: Record the baseline failure so the fix is provable**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: `10 failed, 71 passed, 17 errors` (approximately — the exact count is the baseline, record it).

- [ ] **Step 2: Confirm the root cause explicitly**

```bash
cd /Users/randyren/Desktop/hera
cat .venv/pyvenv.cfg | grep command
ls .venv/lib/python3.14/site-packages/sqlite_vec/
```

Expected: `command = ... -m venv /Users/I771473/Desktop/magnum opus/.venv`, and the `sqlite_vec/` listing shows only `__init__.py` and `__pycache__` — **no `vec0.dylib`**.

- [ ] **Step 3: Delete the stale environment and all bytecode caches**

```bash
cd /Users/randyren/Desktop/hera
rm -rf .venv .pytest_cache .checkpoints/state.json
find . -name '__pycache__' -type d -not -path './.git/*' -exec rm -rf {} +
```

- [ ] **Step 4: Recreate the venv and install dependencies**

`PIP_DEPS` is defined at `scripts/install/venv.py:20` as
`["sqlite-vec", "ulid-py", "requests", "pyyaml", "pytest"]`.

```bash
cd /Users/randyren/Desktop/hera
/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet sqlite-vec ulid-py requests pyyaml pytest
```

Note: the old venv used Python 3.14 from Homebrew. Either 3.13 or 3.14 works, but the interpreter must be a **framework/Homebrew** build — `sqlite-vec` needs `enable_load_extension`, which the macOS system Python does not provide. `scripts/install/venv.py:1354` documents this constraint.

- [ ] **Step 5: Verify sqlite-vec actually loads**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -c "
import sqlite3, sqlite_vec
c = sqlite3.connect(':memory:')
c.enable_load_extension(True)
sqlite_vec.load(c)
print('sqlite-vec OK:', c.execute('select vec_version()').fetchone()[0])
"
```

Expected: `sqlite-vec OK: v0.x.x` — **not** an `OperationalError` about `vec0.dylib`.

- [ ] **Step 6: Re-run the suite and record what actually remains**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -20
```

Expected: the 17 errors are gone. The spec predicts the 10 install/skill/uninstall failures also clear, since they trace to `install: preflight failed` → the `sqlite-vec-loads` check at `install.py:697`. **This is a prediction, not a certainty.** If any failures survive, do not fix them here — record each one (name + assertion) in the commit message and raise them as out-of-scope findings.

- [ ] **Step 7: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add -A
git commit -m "chore: rebuild venv on personal machine

The copied .venv had pyvenv.cfg pointing at the old work-machine path and
was missing sqlite_vec/vec0.dylib, which caused all 17 test errors and,
via the install preflight sqlite-vec check, all 10 install-test failures.
Also cleared stale __pycache__ that was leaking old paths into tracebacks."
```

---

### Task 2: Replace `--bare` with isolation flags

**Files:**
- Modify: `scripts/ingest.py:56-57` (constants), `:87`, `:149`, `:326-343` (call sites)
- Modify: `scripts/publish.py:38-39` (constants), `:76` (call site)
- Test: `tests/test_nested_claude_isolation.py` (create)

**Interfaces:**
- Produces: `ingest.CLAUDE_ISOLATION` and `publish.CLAUDE_ISOLATION`, both `list[str]`; `ingest.CLAUDE_MODEL` / `publish.CLAUDE_MODEL`, both `str` defaulting to `"sonnet"`. Task 3 asserts against these names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nested_claude_isolation.py`:

```python
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
```

`tests/conftest.py` currently defines no `pytest_addoption` (it only holds the
`vault_env` fixture and `count_symlinks`). Append this new function to it,
after the imports and before `_VAULT_COPY`:

```python
def pytest_addoption(parser):
    parser.addoption("--live-llm", action="store_true", default=False,
                     help="run tests that make real Claude subscription calls")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/test_nested_claude_isolation.py -q
```

Expected: FAIL — `AttributeError: module 'ingest' has no attribute 'CLAUDE_ISOLATION'`, and `assert ingest.CLAUDE_MODEL == "sonnet"` fails because it is currently `"claude-opus-latest"`.

- [ ] **Step 3: Add the constants to `scripts/ingest.py`**

Replace line 57:

```python
CLAUDE_MODEL = os.environ.get("HERA_CLAUDE_MODEL", "claude-opus-latest")
```

with:

```python
CLAUDE_MODEL = os.environ.get("HERA_CLAUDE_MODEL", "sonnet")

# Isolation flags for nested `claude -p` calls. These REPLACE --bare.
#
# --bare skipped hooks, but it also skips keychain reads, and its auth is
# strictly ANTHROPIC_API_KEY or apiKeyHelper -- OAuth is never read. On a
# subscription that means every nested call dies with "Not logged in".
#
# --setting-sources "" loads no user/project/local settings, so no hook from
# any source can fire. That is a STRONGER recursion guarantee than --bare
# gave us, which never isolated MCP servers or tools.
CLAUDE_ISOLATION = [
    "--setting-sources", "",      # no settings files -> no hooks
    "--strict-mcp-config",        # no MCP servers
    "--tools", "",                # no filesystem access
    "--disable-slash-commands",
]

# Nested calls run here so CLAUDE.md auto-discovery (which --bare used to
# suppress) finds nothing to inject into the extraction prompt.
CLAUDE_CWD = tempfile.gettempdir()
```

Add `import tempfile` to the imports block at the top of the file, in alphabetical position between `import sys` and `import time`.

- [ ] **Step 4: Update the three `ingest.py` call sites**

At line 87 (`_user_stated_explicitly`), line 149 (`_detect_contradiction`), and line 341 (`_call_claude_extract`), replace:

```python
    cmd = [CLAUDE_BIN, "-p", "--bare", "--output-format", "text", "--model", CLAUDE_MODEL]
```

with:

```python
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
```

and add `cwd=CLAUDE_CWD` to each corresponding `subprocess.run(...)`. The three become:

```python
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=300, cwd=CLAUDE_CWD)   # _user_stated_explicitly
```
```python
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=300, cwd=CLAUDE_CWD)   # _detect_contradiction
```
```python
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=600, cwd=CLAUDE_CWD)   # _call_claude_extract
```

Do not change the timeouts, stdin handling, JSON recovery, or error paths.

- [ ] **Step 5: Fix the stale docstring at `ingest.py:326-339`**

`_call_claude_extract`'s docstring currently explains why `--bare` is used. Replace that paragraph:

```
    We pass --bare to skip Claude Code hooks, plugin sync, CLAUDE.md auto-
    discovery, and other session scaffolding. Without --bare, `claude -p`
    invoked from inside this vault would try to fire our own Stop-hook and
    stop_score.py on completion, which risks recursion and long timeouts.
```

with:

```
    CLAUDE_ISOLATION skips every settings source, so no hook -- ours or
    anyone else's -- can fire, and the call cannot reach MCP servers or the
    filesystem. We deliberately do NOT use --bare: it skips keychain reads,
    so a subscription login is invisible and the call fails "Not logged in".
    cwd=CLAUDE_CWD keeps this vault's CLAUDE.md out of the prompt.
```

- [ ] **Step 6: Apply the same change to `scripts/publish.py` by importing, not duplicating**

`publish.py:31` already reads `import ingest as _ingest`, so the constants are reachable — do **not** define a second copy. Delete line 39 (`CLAUDE_MODEL = os.environ.get("HERA_CLAUDE_MODEL", "claude-opus-latest")`) and replace it with re-exports so the module-level names `publish.CLAUDE_MODEL`, `publish.CLAUDE_ISOLATION`, and `publish.CLAUDE_CWD` still exist (the tests in Step 1 assert on them):

```python
# Nested-call configuration is defined once, in ingest. Re-exported here so
# this module's call site and its tests can refer to the same values.
CLAUDE_MODEL = _ingest.CLAUDE_MODEL
CLAUDE_ISOLATION = _ingest.CLAUDE_ISOLATION
CLAUDE_CWD = _ingest.CLAUDE_CWD
```

These three lines must sit **after** the `import ingest as _ingest` at line 31, not up with the other constants at line 38. Leave `publish.py`'s own `CLAUDE_BIN` definition (line 38) alone — `tests/test_claude_bin_resolve.py::test_publish_bin_resolution` asserts `publish.CLAUDE_BIN` resolves independently, and the Task 3 test reloads the modules separately. No `import tempfile` is needed in `publish.py`.

Then update line 76:

```python
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=600, cwd=CLAUDE_CWD)
```

**Reload caution for the tests:** because `publish` binds these at import time, a test that reloads `ingest` with a patched env must reload `publish` afterwards to pick up the new values. `tests/test_claude_bin_resolve.py::test_teardown_reload` already reloads both.

- [ ] **Step 7: Run the isolation tests**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/test_nested_claude_isolation.py -q
```

Expected: PASS (the `test_live_nested_call_authenticates` test is skipped).

- [ ] **Step 8: Run the live test — this is the one that proves the bug is fixed**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/test_nested_claude_isolation.py --live-llm -q
```

Expected: PASS, all tests, no skips. If this fails with `Not logged in`, the fix is incomplete — do not proceed.

- [ ] **Step 9: Verify no `--bare` survives**

```bash
cd /Users/randyren/Desktop/hera
grep -rn '"--bare"' scripts/ || echo "OK: no --bare in scripts/"
```

Expected: `OK: no --bare in scripts/`. (`scripts/check_team_remote.sh` and `scripts/e2e_publish.sh` contain `git init --bare`, which is unrelated and must not be touched — the grep above matches only the quoted Python string.)

- [ ] **Step 10: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add scripts/ingest.py scripts/publish.py tests/test_nested_claude_isolation.py tests/conftest.py
git commit -m "fix: nested claude -p now authenticates on a subscription

--bare skips keychain reads and accepts only ANTHROPIC_API_KEY or
apiKeyHelper, so every nested call failed 'Not logged in' on an OAuth
subscription. Replace it with --setting-sources '' --strict-mcp-config
--tools '' --disable-slash-commands, which blocks hooks from ALL sources
(stronger than --bare) and additionally isolates MCP and tools, while
leaving auth intact. Run with cwd outside the vault so CLAUDE.md is not
auto-discovered into the prompt.

Also fixes the default model: claude-opus-latest no longer resolves.
Default is now sonnet."
```

---

### Task 3: Update `test_claude_bin_resolve.py` for the new command shape

**Files:**
- Modify: `tests/test_claude_bin_resolve.py`

**Interfaces:**
- Consumes: `ingest.CLAUDE_ISOLATION`, `ingest.CLAUDE_MODEL` from Task 2.

- [ ] **Step 1: Run the existing test to see current state**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/test_claude_bin_resolve.py -q
```

Expected: PASS. This test asserts on `CLAUDE_BIN` resolution and graceful degradation, not on the flags — Task 2 should not have broken it. If it fails, Task 2 changed behavior it should not have; stop and investigate.

- [ ] **Step 2: Add a regression guard tying the two tests together**

Append to `tests/test_claude_bin_resolve.py`:

```python
def test_bin_resolution_survives_isolation_flags(monkeypatch):
    """CLAUDE_BIN resolution is independent of the isolation flags; a change
    to one must not silently break the other."""
    ingest = _reload(monkeypatch, CLAUDE_BIN="/custom/claude")
    assert ingest.CLAUDE_BIN == "/custom/claude"
    assert "--bare" not in ingest.CLAUDE_ISOLATION
    importlib.reload(ingest)
```

- [ ] **Step 3: Run it**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/test_claude_bin_resolve.py -q
```

Expected: PASS, 7 tests.

- [ ] **Step 4: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add tests/test_claude_bin_resolve.py
git commit -m "test: guard CLAUDE_BIN resolution against isolation-flag drift"
```

---

### Task 4: Rewrite the NEVER-5 invariant across the docs

The old invariant mandates the exact thing that broke the install. Every copy must change or a future reader will reintroduce the bug.

**Files:**
- Modify: `CLAUDE.md` (§"What NEVER to do" item 5), `docs/DECISIONS.md:23` + `:39`, `docs/PIPELINES.md:22,24,37,42,58,67,209,219,267`, `docs/ARCHITECTURE.md:170,188`, `.claude/skills/hera-ingest/SKILL.md:48`

- [ ] **Step 1: Find every occurrence**

```bash
cd /Users/randyren/Desktop/hera
grep -rn -- "--bare" CLAUDE.md docs/ .claude/skills/ | grep -v "git init"
```

Expected: roughly 16 lines across 5 files. Work through the list; the greps in Step 6 are the completion check.

- [ ] **Step 2: Rewrite `CLAUDE.md` item 5**

Replace:

```markdown
5. **Never invoke `claude -p ...` from inside an engine without
   `--bare`.** Nested Claude calls inherit this vault's hooks and recurse.
   Every subprocess call in `ingest.py` uses `--bare` — keep it that way.
```

with:

```markdown
5. **Never invoke `claude -p ...` from inside an engine without the
   isolation flags — and never with `--bare`.** Nested Claude calls
   otherwise inherit this vault's hooks and recurse. Every subprocess call
   in `ingest.py` and `publish.py` passes `CLAUDE_ISOLATION`
   (`--setting-sources ""`, `--strict-mcp-config`, `--tools ""`,
   `--disable-slash-commands`) and runs with `cwd=CLAUDE_CWD`. `--bare` is
   **forbidden**: it skips keychain reads and accepts only
   `ANTHROPIC_API_KEY`, so nested calls die with `Not logged in` on a
   subscription.
```

- [ ] **Step 3: Rewrite the `docs/DECISIONS.md` NEVER-5 row (line 23)**

Replace the row body with:

| Field | New content |
|---|---|
| Rule | **Never invoke `claude -p ...` from inside an engine without `CLAUDE_ISOLATION`, and never with `--bare`.** Every subprocess call in `ingest.py` / `publish.py` passes `--setting-sources ""`, `--strict-mcp-config`, `--tools ""`, `--disable-slash-commands` and sets `cwd=CLAUDE_CWD`. |
| Why | Without isolation, a nested call inherits the Stop hook / `stop_score.py` and CLAUDE.md auto-discovery, firing the vault's own loop recursively. `--bare` used to provide this but skips keychain reads, so subscription (OAuth) auth fails outright — it is now forbidden. `--setting-sources ""` is strictly stronger: it excludes hooks from *every* source, not just this vault's. |
| Severity | **CATASTROPHIC** |

Update line 39 (enforcement locations) to:

```
| NEVER-5 | `scripts/ingest.py` (3 nested calls) + `scripts/publish.py` (1 call), all `[CLAUDE_BIN, "-p", *CLAUDE_ISOLATION, "--output-format", "text", "--model", CLAUDE_MODEL]` with `cwd=CLAUDE_CWD`. Guarded by `tests/test_nested_claude_isolation.py`. |
```

- [ ] **Step 4: Update `docs/PIPELINES.md`**

Line 22 — replace the `--bare` paragraph with:

```markdown
- **All nested LLM calls are isolated.** 3 calls in `ingest.py`, 1 in `publish.py`, identical command shape `[CLAUDE_BIN, "-p", *CLAUDE_ISOLATION, "--output-format", "text", "--model", CLAUDE_MODEL]` run with `cwd=CLAUDE_CWD`. `CLAUDE_ISOLATION` is `--setting-sources ""` (load no settings files, so no hook from any source fires), `--strict-mcp-config`, `--tools ""`, and `--disable-slash-commands`. That is what prevents the vault's own Stop hook from firing recursively inside an engine. `--bare` is **not** used: it skips keychain reads, so subscription auth fails.
```

Line 24 — replace with:

```markdown
Model and binary are env-overridable everywhere: `CLAUDE_BIN` (default `claude`), `HERA_CLAUDE_MODEL` (default `sonnet`).
```

Lines 37, 42, 209 are mermaid diagram node labels reading `&#40;--bare&#41;`. Change each to `&#40;isolated&#41;`.

Lines 58, 67, 219 are prose reading `runs \`claude -p --bare\``. Change each to ``runs `claude -p` (isolated)``.

Line 267 — replace the config-table row with:

```markdown
| Claude binary / model | env `CLAUDE_BIN` / `HERA_CLAUDE_MODEL` | defaults `claude` / `sonnet`; all nested calls use `CLAUDE_ISOLATION`, never `--bare` |
```

- [ ] **Step 5: Update `docs/ARCHITECTURE.md`**

Line 170 — change `Every nested \`claude -p\` call uses \`--bare\` to prevent hook recursion.` to:

```markdown
Every nested `claude -p` call uses `CLAUDE_ISOLATION` (no settings sources, no MCP, no tools) to prevent hook recursion; `--bare` is forbidden because it breaks subscription auth.
```

Line 188 — change the invariant-table row to:

```markdown
| Never call `claude -p` without `CLAUDE_ISOLATION` (and never with `--bare`) | Nested calls inherit this vault's hooks and recurse; `--bare` skips keychain reads and breaks subscription auth | Every subprocess call in `ingest.py` / `publish.py` | CATASTROPHIC |
```

Also update `.claude/skills/hera-ingest/SKILL.md:48`, changing ``a `claude -p` call with a strict JSON schema`` to ``an isolated `claude -p` call with a strict JSON schema``.

- [ ] **Step 6: Verify no doc still mandates `--bare`**

```bash
cd /Users/randyren/Desktop/hera
grep -rn -- "--bare" CLAUDE.md docs/ .claude/skills/ scripts/*.py | grep -v "git init"
```

Expected: every remaining line must *forbid* `--bare`, never require it. There should be no line saying "uses `--bare`" or "keep it that way".

- [ ] **Step 7: Run the docs gates**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/check_docs_consistency.sh && bash scripts/check_doc_links.sh && bash scripts/check_mermaid_fences.sh
```

Expected: all three exit 0. `check_docs_consistency.sh` asserts `team_index.py`, `team.db`, and `ADR-14` are still *present* in `README.md`, `docs/PIPELINES.md`, and `docs/ARCHITECTURE.md` — the edits above must not have removed them.

- [ ] **Step 8: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add CLAUDE.md docs/ .claude/skills/hera-ingest/SKILL.md
git commit -m "docs: rewrite NEVER-5 -- isolation flags, not --bare

The old invariant mandated --bare, which is the exact thing that broke
nested calls on a subscription. State the real requirement (isolate from
all setting sources) and record that --bare is forbidden."
```

---

### Task 5: Build the neutral seed fixture pack

The e2e seed scripts hard-depend on `seed/fpa106`. They need a replacement before that directory can be deleted.

**Files:**
- Create: `tests/fixtures/seed-pack/concepts/Tidal Locking.md`, `tests/fixtures/seed-pack/concepts/Spaced Repetition.md`, `tests/fixtures/seed-pack/entities/Anki.md`, `tests/fixtures/seed-pack/entities/SQLite.md`

**Interfaces:**
- Produces: a 4-page pack (2 concepts, 2 entities) with baked ULIDs, `tags: [seed]`, `pinned: true`. Task 6 indexes it and asserts counts of 4. One page (`spaced-repetition.md`) carries a numeric claim that Task 6's conflict test contradicts.

- [ ] **Step 1: Create the concept pages**

`tests/fixtures/seed-pack/concepts/Tidal Locking.md`:

```markdown
---
id: 01JZZSEEDPACKTDAKCKNG00000
type: concept
title: "Tidal Locking"
aliases: ["Synchronous rotation"]
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
> [!info] A body's rotation period matching its orbital period, so it always shows the same face to its partner.

Tidal locking happens when gravitational gradients across a body dissipate its rotational energy until its spin period equals its orbital period. The Moon is tidally locked to Earth, which is why the near side is the only side visible from the ground.

The timescale depends strongly on orbital distance, so close-in bodies lock fast. See [[SQLite]] for an unrelated page used to exercise multi-type indexing.
```

`tests/fixtures/seed-pack/concepts/Spaced Repetition.md`:

```markdown
---
id: 01JZZSEEDPACKSPACEDRP00000
type: concept
title: "Spaced Repetition"
aliases: ["SRS"]
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
> [!info] Scheduling reviews at increasing intervals so each recall happens near the point of forgetting.

Spaced repetition schedules a review just before predicted forgetting, so each successful recall pushes the next interval further out. The classic SM-2 algorithm uses **six** interval steps before a card is considered mature.

Implementations store scheduling state per card; [[Anki]] is the most widely used.
```

The "**six** interval steps" claim is deliberate — Task 6's conflict test contradicts it with "five".

- [ ] **Step 2: Create the entity pages**

`tests/fixtures/seed-pack/entities/Anki.md`:

```markdown
---
id: 01JZZSEEDPACKANKAPPZZ00000
type: entity
title: "Anki"
aliases: []
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
> [!info] An open-source spaced-repetition flashcard application.

Anki is a flashcard program built around [[Spaced Repetition]]. It stores its collection in an [[SQLite]] database.
```

`tests/fixtures/seed-pack/entities/SQLite.md`:

```markdown
---
id: 01JZZSEEDPACKSQTEDBZZ00000
type: entity
title: "SQLite"
aliases: ["sqlite3"]
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
> [!info] An embedded, serverless, single-file SQL database engine.

SQLite runs in-process with no separate server. It is the storage layer for [[Anki]] and for this vault's own index.
```

- [ ] **Step 3: Verify the pack indexes cleanly into a throwaway DB**

```bash
cd /Users/randyren/Desktop/hera
TMPDB=$(mktemp -d)/hera.db
HERA_DB="$TMPDB" .venv/bin/python scripts/hera_db.py --init >/dev/null
HERA_DB="$TMPDB" .venv/bin/python scripts/seed_index.py tests/fixtures/seed-pack --json
```

Expected: `{"indexed": 4, "skipped": 0, "pinned": 4}`.

- [ ] **Step 4: Clean up the wiki files the check just wrote**

`seed_index` copies pages into `wiki/`. The template wiki ships empty, so remove them:

```bash
cd /Users/randyren/Desktop/hera
rm -f "wiki/concepts/Tidal Locking.md" "wiki/concepts/Spaced Repetition.md" \
      "wiki/entities/Anki.md" "wiki/entities/SQLite.md"
git status --short wiki/
```

Expected: no untracked files under `wiki/concepts/` or `wiki/entities/`.

- [ ] **Step 5: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add tests/fixtures/seed-pack
git commit -m "test: add neutral 4-page seed fixture pack

Replaces seed/fpa106 as the fixture for the seed e2e scripts, so the
SAP pack can be deleted without losing coverage of the indexer,
idempotency, the pinned prune exemption, and the pinned-wins conflict
path."
```

---

### Task 6: Repoint the seed e2e scripts at the fixture pack

**Files:**
- Modify: `scripts/e2e_seed_index.sh` (`PACK`, `EXPECT`, placement counts)
- Modify: `scripts/e2e_seed_guards.sh` (`PACK`, `EXPECT`, the whole `conflict` subcommand payload)

**Interfaces:**
- Consumes: `tests/fixtures/seed-pack/` from Task 5.

- [ ] **Step 1: Update `scripts/e2e_seed_index.sh`**

Change the header comment's `seed/fpa106` to `tests/fixtures/seed-pack`, then change:

```bash
PACK="seed/fpa106"
EXPECT=137
```

to:

```bash
PACK="tests/fixtures/seed-pack"
EXPECT=4
```

The script already derives `EXP_CONCEPTS` / `EXP_ENTITIES` by counting files in the pack, so the placement assertions need no change (they become 2 and 2).

- [ ] **Step 2: Run it**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/e2e_seed_index.sh
```

Expected: exits 0, final line `== e2e_seed_index: OK (4/4/4, idempotent, pinned) ==`. Update that echo string from `137/137/137` to `4/4/4`.

- [ ] **Step 3: Update `scripts/e2e_seed_guards.sh` header and constants**

Change the header comment's `seed/fpa106` to `tests/fixtures/seed-pack`, then:

```bash
PACK="tests/fixtures/seed-pack"
EXPECT=4
```

- [ ] **Step 4: Replace the `prune` subcommand's seed-title marker**

The prune subcommand asserts no known seed titles leak into the candidate list. Replace:

```python
seed_markers = {"8 Cloud Quality Categories", "ARC Period", "Ask Mode"}
```

with:

```python
seed_markers = {"Spaced Repetition", "Tidal Locking", "Anki", "SQLite"}
```

- [ ] **Step 5: Run the prune guard**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/e2e_seed_guards.sh prune
```

Expected: exits 0, `== e2e_seed_guards[prune]: OK ==`.

- [ ] **Step 6: Rewrite the `conflict` subcommand payload**

This subcommand contradicts a real seed claim. Replace `TARGET_TITLE`:

```bash
  TARGET_TITLE="Spaced Repetition"
```

Replace the `NOTE` heredoc:

```bash
  cat > "$NOTE" <<'TXT'
Correction on spaced repetition: the SM-2 algorithm uses FIVE interval steps
before a card is considered mature, not six. The sixth step was removed from
the reference implementation.
TXT
```

Replace the `_fake_extract` payload's `source` and `concepts` blocks:

```python
def _fake_extract(raw):
    return {
        "source": {
            "title": "User Correction On SM-2 Interval Steps",
            "one_line": "A user note correcting the SM-2 interval step count.",
            "key_takeaways": ["SM-2 uses five interval steps, not six."],
            "body": raw,
        },
        "concepts": [{
            "title": target_title,
            "one_line": "Scheduling reviews at increasing intervals near the point of forgetting.",
            "body": ("Spaced repetition schedules a review just before predicted "
                     "forgetting. The SM-2 algorithm uses FIVE interval steps "
                     "before a card is considered mature; the sixth step was "
                     "removed from the reference implementation."),
            "aliases": [],
        }],
        "entities": [],
        "warnings": [],
    }
```

Replace the body assertion:

```python
low = body.lower()
assert ("five" in low or "sixth step was removed" in low), \
    "FAIL: page body does not carry the user's new claim (not user-wins)"
```

Everything else in the subcommand — the pinned check, the open-conflict assertions, the `resolved_new` check, the archive/duplicate-row checks — is content-independent and stays.

- [ ] **Step 7: Run the conflict guard**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/e2e_seed_guards.sh conflict
```

Expected: exits 0, `== e2e_seed_guards[conflict]: OK ==`. This makes one real contradiction-detection LLM call, so it also independently exercises the Task 2 auth fix.

- [ ] **Step 8: Confirm the working tree is clean**

```bash
cd /Users/randyren/Desktop/hera
git status --short
```

Expected: only the two modified `e2e_seed_*.sh` files. Both scripts restore `wiki/{hot,index,log}.md` and delete the pages they created; if anything else shows up, their cleanup traps have a gap — fix that before committing.

- [ ] **Step 9: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add scripts/e2e_seed_index.sh scripts/e2e_seed_guards.sh
git commit -m "test: repoint seed e2e scripts at the neutral fixture pack"
```

---

### Task 7: Delete the SAP seed pack

**Files:**
- Delete: `seed/fpa106/` (137 pages), `scripts/build_seed_pack.py`
- Modify: `seed/README.md`, `scripts/seed_index.py:3,191`, `.claude/skills/hera-setup/SKILL.md` (step 7)

**Interfaces:**
- Consumes: Task 6's repointed e2e scripts. Do not run this before Task 6, or the e2e scripts break.

- [ ] **Step 1: Delete the pack and its builder**

```bash
cd /Users/randyren/Desktop/hera
git rm -r --quiet seed/fpa106
git rm --quiet scripts/build_seed_pack.py
```

`build_seed_pack.py` exists solely to regenerate `seed/fpa106` from a corporate source clone. With the pack gone it has nothing to operate on.

- [ ] **Step 2: Rewrite `seed/README.md`**

Replace the entire file with:

```markdown
# Seed packs

A **seed pack** is an optional, pre-built set of knowledge pages you can load
into a fresh vault in one shot. No pack ships with Hera — this directory
documents the format so you can build your own.

Loading a pack is always a deliberate act:

```
python scripts/hera_cli.py seed_index <pack_dir>
```

It writes only to the personal `hera.db` — never `team.db`.

## Pack format

```
<pack_dir>/
  concepts/*.md
  entities/*.md
```

**Concept and entity pages only.** No `sources/` — a pack carries distilled
knowledge, not source provenance.

Each page needs this frontmatter:

```yaml
---
id: 01J...            # a ULID, baked and permanent — see below
type: concept         # or: entity
title: "Page Title"
aliases: ["Alt name"]
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
```

## Stable ULIDs — do NOT regenerate

Every page carries a baked `id:`. **ULIDs are the page's permanent address** —
the indexer keys on them so re-running is idempotent (no duplicate rows).
Never rewrite, regenerate, or strip these IDs. Doing so breaks idempotency and
re-introduces duplicate index rows.

## What `tags: [seed]` and `pinned: true` buy you

- **Exempt from `/hera-prune`.** Pinned pages never enter the candidate band.
- **User content always wins.** A later note that contradicts a pinned page
  auto-resolves user-wins rather than freezing an open conflict, so a pack
  never blocks you with a conflict queue.

## Testing a pack

`tests/fixtures/seed-pack/` is a 4-page reference pack used by
`scripts/e2e_seed_index.sh` and `scripts/e2e_seed_guards.sh`. Copy its shape.
```

- [ ] **Step 3: De-fpa106 `scripts/seed_index.py`**

Line 3 — change `Indexes a seed directory (e.g. seed/fpa106) into the personal brain index by` to:

```
Indexes a seed directory (e.g. tests/fixtures/seed-pack) into the personal
brain index by
```

Line 191 — change the argparse help:

```python
    ap.add_argument("pack_dir",
                    help="seed pack directory (e.g. tests/fixtures/seed-pack)")
```

- [ ] **Step 4: Remove step 7 from `.claude/skills/hera-setup/SKILL.md`**

Delete the entire numbered step 7 block (the `**Seed pack: fpa106 SAP knowledge (optional).**` heading through the line ending `rather than freezing a conflict.`), then renumber the following step 8 ("Global CLAUDE.md (optional)") to **7**, and renumber any steps after it accordingly.

Setup no longer asks about seeding at all. If the skill's preamble states a step count, update it.

- [ ] **Step 5: Verify no reference to the deleted pack survives**

```bash
cd /Users/randyren/Desktop/hera
grep -rn "fpa106\|build_seed_pack" --include="*.py" --include="*.sh" --include="*.md" \
  . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=superpowers \
  || echo "OK: no fpa106 or build_seed_pack references"
```

Expected: `OK: ...`, or hits only inside `docs/superpowers/` (the spec and this plan, which describe the removal and are allowed to name it).

- [ ] **Step 6: Re-run the seed e2e scripts to prove nothing depended on the pack**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/e2e_seed_index.sh && bash scripts/e2e_seed_guards.sh prune && bash scripts/e2e_seed_guards.sh conflict
```

Expected: all three exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add -A seed scripts/seed_index.py .claude/skills/hera-setup/SKILL.md
git commit -m "feat: remove the SAP seed pack, keep the seed mechanism

Deletes seed/fpa106 (137 SAP release-ops pages) and build_seed_pack.py,
its corporate-source rewriter. seed_index.py, the hera_cli entry point,
and the pack format survive so a personal pack can be built later.

/hera-setup no longer prompts about seeding — it is now a deliberate
hera_cli.py seed_index <pack> run."
```

---

### Task 8: Rename `seed_wins` → `pinned_wins`

Pure rename. The branch reads `pages.pinned`, so it was never seed-specific — it is a generic "a pinned page loses to new user content" rule that happened to be named after its first use case.

**Files:**
- Modify: `scripts/ingest.py:615-645`

- [ ] **Step 1: Apply the rename**

At `scripts/ingest.py:619`, change:

```python
                seed_wins = bool(conn.execute(
                    "SELECT pinned FROM pages WHERE id=?", (old_id,)
                ).fetchone()[0])
```

to:

```python
                pinned_wins = bool(conn.execute(
                    "SELECT pinned FROM pages WHERE id=?", (old_id,)
                ).fetchone()[0])
```

At line 626, change `if session_explicit or seed_wins:` to `if session_explicit or pinned_wins:`.

At line 641, change:

```python
                    reason = ("seed override" if seed_wins
                              else "ADR-11 explicit statement")
```

to:

```python
                    reason = ("pinned override" if pinned_wins
                              else "ADR-11 explicit statement")
```

- [ ] **Step 2: Reword the comment above the branch (lines 613-617)**

Replace:

```
                # Seed exception (Phase 4): if the EXISTING page is a pinned
                # seed page, the user's new content always wins — take the same
                # ADR-11 resolve_new path (no open conflict, page updated in
                # place, not left frozen/stale) rather than enqueuing.
```

with:

```
                # Pinned exception: if the EXISTING page is pinned (seed packs
                # pin their pages, but anything may be pinned), the user's new
                # content always wins — take the same ADR-11 resolve_new path
                # (no open conflict, page updated in place, not left
                # frozen/stale) rather than enqueuing.
```

- [ ] **Step 3: Verify no stale identifier remains**

```bash
cd /Users/randyren/Desktop/hera
grep -n "seed_wins\|seed override" scripts/ingest.py || echo "OK: renamed"
```

Expected: `OK: renamed`.

- [ ] **Step 4: Prove behavior is unchanged**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/e2e_seed_guards.sh conflict
.venv/bin/python -m pytest tests/test_ingest_conflicts.py -q
```

Expected: both pass. The e2e conflict guard drives this exact branch.

- [ ] **Step 5: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add scripts/ingest.py
git commit -m "refactor: rename seed_wins -> pinned_wins

The branch reads pages.pinned and was never seed-specific. No behavior
change; the e2e conflict guard drives this exact path."
```

---

### Task 9: Strip corporate identifiers and rename to Hera

**Files:**
- Modify: `README.md:2,104,161-164`, `docs/README.md:65,68`, `docs/DECISIONS.md:117,119`, `docs/GLOBAL_INSTALL.md:78`, `tests/test_hookcmd.py:12`, `.claude/settings.local.json:4-5`

**Interfaces:**
- Consumes: Task 7 (the seed paragraphs being removed reference the now-deleted pack).

- [ ] **Step 1: Confirm the exact remaining set**

```bash
cd /Users/randyren/Desktop/hera
git ls-files -z | xargs -0 grep -linE "\b(sap|fpa106|harca|qrc|i771473|wdf|successfactors)\b|magnum opus|s-4hana" 2>/dev/null | grep -v "^docs/superpowers/"
```

Expected after Task 7: `README.md`, `docs/README.md`, `docs/DECISIONS.md`, `docs/GLOBAL_INSTALL.md`, `tests/test_hookcmd.py`.

**Word boundaries matter.** Without `\b`, `sap` matches `di`**`sap`**`peared`, which falsely flagged `docs/PIPELINES.md`, `docs/DATA-MODEL.md`, `docs/RETRIEVAL.md`, and `scripts/team_index.py` in the original audit. Those four files have **no** corporate content — do not edit them for this task.

- [ ] **Step 2: Fix `README.md`**

Line 2 — change the logo alt text:

```html
  <img src="public/orca.png" width="160" alt="Hera">
```

("Orca" is the corporate project codename. The image is neutral artwork and stays.)

Line 104 — the clone URL. **User decision: the git remote is being removed entirely, so there is no URL to point at.** Replace the clone step with a placeholder that does not imply a published repo. The block at README.md:101-114 becomes:

```bash
# 1. Get the repo onto the machine (clone your own remote, or copy the
#    directory across — this vault is personal, not a published template).
cd ~/hera

# 2. Bootstrap the machine: venv, Ollama (install + start + model),
#    hera.db, and the global hooks + skills. On a fresh machine this
#    prompts "install Ollama now? [Y/n]" — press Enter to accept.
python install.py

# 3. Launch Claude Code from ANY directory and finish scaffolding.
#    Then, inside Claude Code, run: /hera-setup
claude
```

Do not invent a GitHub URL. Do not add `git remote add` instructions.

Line 5 — change the title heading:

```html
<h1 align="center">Hera</h1>
```

Add or keep a descriptive subtitle beneath it, e.g. "a personal second brain for Claude Code". The phrase "second brain" is the product category and is fine to keep in prose (line 124 reads "turns the second brain into a **global**…" — leave it, or reword to "turns Hera into"). `README.md` contains **no** "magnum opus" — that string is only in `docs/GLOBAL_INSTALL.md:78`.

Lines 161-164 — delete list item 4 entirely (the fpa106 pre-seed description) and renumber any following items.

Do not remove the strings `team_index.py` or `team.db`; `check_docs_consistency.sh` asserts they are present in `README.md`.

- [ ] **Step 3: Fix `docs/README.md` lines 65 and 68**

Replace the `seed/README.md` bullet with:

```markdown
- `seed/README.md`: the **seed pack** format. No pack ships with Hera; the doc
  describes the layout so you can build your own and load it with
  `python scripts/hera_cli.py seed_index <pack_dir>`. Its pages are pinned
  (exempt from `/hera-prune`) and lose to later user notes on contradiction.
```

- [ ] **Step 4: Fix `docs/DECISIONS.md` lines 117 and 119**

Line 117 — change `it tracks the shared \`fpa106-team-brain\` remote, not \`second-brain\`` to:

```markdown
it tracks whatever `HERA_TEAM_REMOTE` points at, not this vault's own remote
```

Line 119 — remove the corporate username from the orphan-folder example. Change `produced an **orphan folder** (\`I771473/.gitkeep\`) that no writer ever touched` to:

```markdown
produced an **orphan folder** (named after the git author, e.g. `<git-author>/.gitkeep`) that no writer ever touched
```

- [ ] **Step 5: Fix `docs/GLOBAL_INSTALL.md` line 78**

Change `This repo (\`magnum opus\`) is the *source/template* that others clone.` to:

```markdown
This repo (`hera`) is the *source/template* that others clone.
```

- [ ] **Step 6: Fix `tests/test_hookcmd.py` line 12**

```python
VAULT = pathlib.Path("/Users/dev/hera vault")  # a path with a space, on purpose
```

The space is deliberate — it is what the test exercises. Keep it.

- [ ] **Step 7: Fix `.claude/settings.local.json` (untracked — verify by hand)**

Change lines 4-5:

```json
      "Read(//Users/randyren/.claude/**)",
      "Read(//Users/randyren/.claude/hooks/**)",
```

This file is gitignored, so the Step 8 audit will never catch it. Confirm visually.

- [ ] **Step 8: Run the audit — it must be empty**

```bash
cd /Users/randyren/Desktop/hera
git ls-files -z | xargs -0 grep -linE "\b(sap|fpa106|harca|qrc|i771473|wdf|successfactors)\b|magnum opus|s-4hana" 2>/dev/null | grep -v "^docs/superpowers/"
echo "exit: $?"
```

Expected: no output before `exit: 1` (grep finding nothing). Hits under `docs/superpowers/` are the spec and this plan, which legitimately name what was removed.

```bash
grep -rn "I771473" .claude/settings.local.json || echo "OK: settings.local.json clean"
```

- [ ] **Step 9: Run the docs gates**

```bash
cd /Users/randyren/Desktop/hera
bash scripts/check_docs_consistency.sh && bash scripts/check_doc_links.sh && bash scripts/check_mermaid_fences.sh
.venv/bin/python -m pytest tests/test_hookcmd.py tests/test_docs_windows.py -q
```

Expected: all exit 0 / PASS.

- [ ] **Step 10: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add README.md docs/README.md docs/DECISIONS.md docs/GLOBAL_INSTALL.md tests/test_hookcmd.py
git commit -m "docs: strip corporate identifiers, rename project to Hera

Removes the SAP corporate git remote, the I771473 username, the fpa106
remote name, and the magnum opus codename. Note the audit regex needs
word boundaries: without them 'sap' matches 'disappeared', which falsely
flagged four clean files."
```

---

### Task 10: Wipe the carried-over vault data and verify everything

Last, so earlier tasks can still read the old DB if a question comes up.

**Files:**
- Delete: `hera.db*`, `team.db*`, `.hera/session-*.md`, `.hera/*.log`, `wiki/.raw/articles/session-*.md`, `wiki/.archive/*`
- Modify: `wiki/hot.md`, `wiki/index.md`, `wiki/log.md`, `wiki/overview.md`

All of these are gitignored (`.gitignore` lines 2, 8-14), so this produces almost no git churn.

- [ ] **Step 1: Back up before deleting — there is no git undo**

```bash
cd /Users/randyren/Desktop/hera
SCRATCH="/private/tmp/claude-501/-Users-randyren-Desktop-hera/23b766bb-6fa4-4707-801b-21fadd57ae02/scratchpad"
mkdir -p "$SCRATCH/vault-backup"
cp hera.db team.db "$SCRATCH/vault-backup/" 2>/dev/null
cp -r .brain "$SCRATCH/vault-backup/" 2>/dev/null
ls -la "$SCRATCH/vault-backup/"
```

Expected: `hera.db` (~3.3 MB), `team.db` (~151 KB), and a `.hera/` directory.

- [ ] **Step 2: Delete the indexes and session artifacts**

```bash
cd /Users/randyren/Desktop/hera
rm -f hera.db hera.db-wal hera.db-shm team.db team.db-wal team.db-shm
rm -f .hera/session-*.md .hera/scorer.log .hera/filing.log
rm -f wiki/.raw/articles/session-*.md
rm -f wiki/.archive/*.md
```

`wiki/meta/r1-verdict.md` **stays** — it is a design record (why Tier-1 citation scoring is disabled), referenced from `CLAUDE.md`, not work content.

- [ ] **Step 3: Reset the meta pages to empty scaffolding**

Write `wiki/hot.md`:

```markdown
# Hot

_Nothing yet. This page is rewritten on every ingest._
```

Write `wiki/index.md`:

```markdown
# Index

_Pages are appended here as they are created._
```

Write `wiki/log.md`:

```markdown
# Log

_Ingest entries are prepended here, newest first._
```

**Leave `wiki/overview.md` untouched.** It already reads "A general-purpose personal knowledge base for notes, articles, and ideas worth remembering and connecting over time" — no work-specific content. It is the anchor every extraction step uses to decide what is worth remembering, so changing it changes ingest behavior for no reason.

- [ ] **Step 4: Regenerate an empty `hera.db`**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python scripts/hera_db.py --init
.venv/bin/python scripts/hera_db.py --doctor
```

Expected: `--doctor` reports all checks passing.

- [ ] **Step 5: Confirm the vault is genuinely empty**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -c "
import pathlib, sys
sys.path.insert(0, 'scripts')
import hera_db
c = hera_db.connect(pathlib.Path('hera.db'))
for t in ('pages', 'pages_fts', 'pages_vec', 'citations', 'conflicts'):
    print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
"
```

Expected: `0` for every table.

- [ ] **Step 6: Run the full test suite — success criterion 1**

```bash
cd /Users/randyren/Desktop/hera
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -20
```

Expected: **0 failed, 0 errors.** Baseline was 10 failed, 71 passed, 17 errors.

- [ ] **Step 7: Run every e2e and check script — success criterion 2**

```bash
cd /Users/randyren/Desktop/hera
for s in scripts/e2e_*.sh tests/check_*.sh; do
  printf '%-50s' "$s"
  if bash "$s" >/dev/null 2>&1; then echo PASS; else echo "FAIL ($?)"; fi
done
```

Expected: every line `PASS`. Note `scripts/e2e_seed_guards.sh` needs a subcommand — run it separately as `prune` and `conflict`. Any script needing a configured team remote is expected to no-op cleanly; if one fails for that reason, confirm it is a clean no-op rather than a real failure.

- [ ] **Step 8: Live ingest — success criterion 4, the one that actually matters**

```bash
cd /Users/randyren/Desktop/hera
cat > /tmp/hera-smoke.md <<'EOF'
# Reciprocal Rank Fusion

Reciprocal Rank Fusion (RRF) merges ranked lists by summing 1/(k + rank) for
each item across lists, with k typically 60. Because it uses rank position
rather than raw score, it fuses rankers whose scores are on incomparable
scales — such as BM25 keyword relevance and dense-vector cosine similarity —
without any normalization step.
EOF
.venv/bin/python scripts/ingest.py /tmp/hera-smoke.md
```

Expected: completes without `Not logged in` or `claude -p failed`. Then verify the write path end-to-end:

```bash
cd /Users/randyren/Desktop/hera
ls wiki/sources/ wiki/concepts/
.venv/bin/python -c "
import pathlib, sys
sys.path.insert(0, 'scripts')
import hera_db
c = hera_db.connect(pathlib.Path('hera.db'))
for t in ('pages', 'pages_fts', 'pages_vec'):
    print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
"
head -20 wiki/hot.md
```

Expected: a source page and at least one concept page exist with ULID frontmatter; `pages`, `pages_fts`, and `pages_vec` counts match and are non-zero; `wiki/hot.md` has been rewritten with real content.

**If this step fails, the reported bug is not fixed** — no amount of green unit tests substitutes for it.

- [ ] **Step 9: Decide what to do with the smoke-test pages**

The smoke ingest wrote real pages into the vault. Either keep them (RRF is genuinely relevant to this vault) or remove them for a truly empty start:

```bash
cd /Users/randyren/Desktop/hera
# To remove: delete the created pages, then re-init.
# rm -f wiki/sources/*.md wiki/concepts/*.md wiki/.raw/articles/hera-smoke.md
# rm -f hera.db && .venv/bin/python scripts/hera_db.py --init
rm -f /tmp/hera-smoke.md
```

Ask the user which they want rather than deciding.

- [ ] **Step 10: Final audit — success criterion 5**

```bash
cd /Users/randyren/Desktop/hera
git ls-files -z | xargs -0 grep -linE "\b(sap|fpa106|harca|qrc|i771473|wdf|successfactors)\b|magnum opus|s-4hana" 2>/dev/null | grep -v "^docs/superpowers/"
echo "audit exit: $?"
git status --short
```

Expected: no files listed. `git status` should show only intended changes.

- [ ] **Step 11: Commit**

```bash
cd /Users/randyren/Desktop/hera
git add -A
git commit -m "chore: reset vault to an empty personal state

Wipes the work machine's hera.db, team.db, session files, and archived
pages, and resets the meta pages to empty scaffolding. All wiped paths
are gitignored. wiki/meta/r1-verdict.md is kept — it is a design record,
not work content.

Verified: full pytest suite green, all e2e scripts pass, and a live
/hera-ingest completes end-to-end on the subscription."
```

---

## Verification Summary

Mapped to the spec's §6 success criteria:

| # | Criterion | Where verified |
|---|---|---|
| 1 | `pytest tests/ -q` → 0 failed, 0 errors | Task 10 Step 6 |
| 2 | All `e2e_*.sh` and `check_*.sh` exit 0 | Task 10 Step 7 |
| 3 | `hera_db.py --doctor` clean | Task 10 Step 4 |
| 4 | Live `/hera-ingest` completes on the subscription | Task 10 Step 8 (also Task 2 Step 8, Task 6 Step 7) |
| 5 | Corporate audit grep empty | Task 10 Step 10 (also Task 9 Step 8) |
| 6 | `/hera-setup` never prompts about seed data | Task 7 Step 4 |

## Out-of-Scope Findings Protocol

The spec flags one open risk: the 10 install/skill/uninstall test failures are *diagnosed* as venv-cascade, not confirmed. If any survive Task 1, they are pre-existing bugs. Record each (test name + assertion), report them, and do **not** fix them inside this plan — scaling scope is the user's call.
