# Hera — corporate-to-personal vault reset

**Date:** 2026-08-12
**Status:** approved, not yet implemented

Port this vault from a work machine to a personal laptop. Three real
problems, one cosmetic one:

1. Nested `claude -p` calls fail on an Anthropic subscription, which breaks
   ingest, contradiction detection, and publish — the vault's entire write
   path.
2. The test suite reports 10 failures and 17 errors.
3. The shipped seed pack is SAP release-operations knowledge that is
   irrelevant on a personal machine.
4. Corporate identifiers (SAP, fpa106, HARCA, QRC, `I771473`, "magnum opus")
   appear in 14 tracked files.

---

## 1. Scope

Five workstreams, in dependency order. The environment rebuild comes first
because nothing else is verifiable until it lands.

| # | Workstream | Why first/last |
|---|---|---|
| 1 | Environment rebuild | Stale `.venv` causes all 17 test errors; blocks verification of everything else |
| 2 | Nested LLM call fix | The actual reported bug |
| 3 | Seed pack removal | Independent of 1–2 |
| 4 | De-corporatization | Slowest; docs examples need rewriting, not substituting |
| 5 | Data wipe | Last, so earlier steps can still read the old DB if a question comes up |

### Non-goals

- **The team-brain subsystem stays.** It no-ops cleanly when
  `SECOND_BRAIN_TEAM_REMOTE` is unset, and it remains useful for
  collaborating with friends on a personal project. No files removed, no
  ADR-14 change, its tests stay in the suite.
- No refactoring of search, ranking, locking, or the hook protocol.
- No new features.
- No renaming of `SECOND_BRAIN_VAULT`, `SECOND_BRAIN_OFF`, or
  `~/.claude/second-brain.env`. Renaming them breaks the existing global
  install for no benefit.

---

## 2. Nested LLM call fix

### Root cause

Two independent bugs, both confirmed by reproduction.

**Bug 1 — `--bare` cannot authenticate a subscription.**

```
$ echo "Say OK" | claude -p --bare --output-format text
Not logged in · Please run /login

$ echo "Say OK" | claude -p --output-format text
OK
```

From `claude --help`:

> `--bare` Minimal mode: skip hooks, LSP, plugin sync, attribution,
> auto-memory, background prefetches, **keychain reads**, and CLAUDE.md
> auto-discovery. Sets `CLAUDE_CODE_SIMPLE=1`. **Anthropic auth is strictly
> `ANTHROPIC_API_KEY` or `apiKeyHelper` via `--settings` (OAuth and keychain
> are never read).**

Subscription OAuth credentials live in the macOS Keychain under
`Claude Code-credentials` → `claudeAiOauth`. `--bare` never reads them, so
every nested call fails regardless of login state. An `ANTHROPIC_API_KEY`
would work, but it bills separately from the subscription.

**Bug 2 — the default model no longer resolves.**

```
$ echo "Say OK" | claude -p --output-format text --model claude-opus-latest
There's an issue with the selected model (claude-opus-latest). It may not
exist or you may not have access to it.
```

`BRAIN_CLAUDE_MODEL` defaults to `claude-opus-latest` in both `ingest.py:57`
and `publish.py:39`. The `sonnet` and `opus` aliases both resolve correctly.

### Fix

Replace `--bare` with explicit isolation flags. Verified working:

```
$ echo 'Return ONE JSON object: {"ok": true}. Nothing else.' \
    | claude -p --setting-sources "" --strict-mcp-config --tools "" \
             --disable-slash-commands --output-format text --model sonnet
{"ok": true}
```

Add to both `scripts/ingest.py` and `scripts/publish.py`:

```python
CLAUDE_MODEL = os.environ.get("BRAIN_CLAUDE_MODEL", "sonnet")

# Isolation flags: replaces --bare. --bare skipped keychain reads, which
# broke subscription (OAuth) auth entirely. These achieve the same
# recursion-safety without touching auth.
CLAUDE_ISOLATION = [
    "--setting-sources", "",      # load no user/project/local settings → no hooks
    "--strict-mcp-config",        # no MCP servers
    "--tools", "",                # no filesystem access
    "--disable-slash-commands",
]
```

### Call sites

Four, all sharing one command shape:

| Site | Purpose | Timeout |
|---|---|---|
| `ingest.py:87` | `_user_stated_explicitly` (ADR-11) | 300 |
| `ingest.py:149` | `_detect_contradiction` | 300 |
| `ingest.py:341` | `_call_claude_extract` | 600 |
| `publish.py:76` | `_strip_body` redactor | 600 |

Each becomes:

```python
cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
       "--output-format", "text", "--model", CLAUDE_MODEL]
```

Every `subprocess.run` additionally gains `cwd=<tempdir>`. Dropping `--bare`
re-enables CLAUDE.md auto-discovery; running from a directory with no
CLAUDE.md keeps the vault's own instructions out of the extraction prompt.
Timeouts, stdin handling, JSON recovery, and error paths are unchanged.

### Why this is safe

`--setting-sources ""` loads no user, project, or local settings, so no hook
from any source can fire. This is a **stronger** recursion guarantee than
`--bare` provided: `--bare` skipped hooks but never isolated MCP servers or
tools. `--tools ""` additionally prevents the nested call from touching the
filesystem.

### Rejected alternatives

- **`--bare` + `apiKeyHelper`.** `--bare` accepts only `ANTHROPIC_API_KEY` or
  an api-key helper. A subscription OAuth token is not an API key and uses a
  different auth path. This requires a real API key with separate metered
  billing.
- **Local Ollama for extraction.** Ollama is already a dependency for
  `nomic-embed-text`, so this is cheap and fully offline. Rejected because
  extraction quality drives page quality directly: a small local model
  produces worse concept splits and far more contradiction false positives,
  and false positives flood the conflict queue. Fidelity is a requirement.
- **`BRAIN_LLM_BACKEND` switch.** A second code path nothing exercises.

### Invariant change

NEVER-5 currently reads *"Never invoke `claude -p ...` from inside an engine
without `--bare`."* Followed literally, that rule is the bug. It is rewritten
to state the actual requirement — isolate nested calls from all setting
sources — and to record that `--bare` is now forbidden because it breaks
subscription auth.

Locations to update:

- `CLAUDE.md` §"What NEVER to do" item 5
- `docs/DECISIONS.md` NEVER-5 row + the enforcement-location row
- `docs/PIPELINES.md` lines 22, 24, 37, 42, 58, 67, 209, 219, 267
- `docs/ARCHITECTURE.md` lines 170, 188
- `.claude/skills/brain-ingest/SKILL.md` line 48

### Verification

New `tests/test_nested_claude_isolation.py` asserts, by reading the module
constants and patching `subprocess.run`:

1. No call site passes `--bare`.
2. All four call sites pass every flag in `CLAUDE_ISOLATION`.
3. All four call sites pass a `cwd` that is not inside the vault.
4. `CLAUDE_MODEL` defaults to `sonnet` and honours `BRAIN_CLAUDE_MODEL`.

Plus one live smoke test gated behind an env var (`BRAIN_LIVE_LLM_TEST=1`)
so a machine without a subscription does not fail the suite.

`tests/test_claude_bin_resolve.py` already asserts on the command shape and
will need updating in step with this change.

---

## 3. Seed pack removal

### Delete

- `seed/fpa106/` — 81 concept pages, 56 entity pages of SAP release-ops
  knowledge.
- `scripts/build_seed_pack.py` — the pack's deterministic rewriter. It exists
  to reproduce `seed/fpa106` from a corporate source clone; with the pack
  gone it has no purpose, and a personal pack would need a different builder.

### Keep

- `scripts/seed_index.py` — the indexer. Still works against any pack.
- `brain_cli.py seed_index` — the entry point.
- `seed/README.md` — rewritten to document how to build your own pack.
  The fpa106 description, page counts, and "reproducing the pack" section
  are replaced by the pack format contract: `concepts/` and `entities/`
  directories, baked stable ULIDs in frontmatter, `pinned: true` and
  `tags: [seed]`, no `sources/`.

### `/brain-setup` change

The setup skill currently prompts *"Pre-seed fpa106 SAP knowledge (release
ops, QRC waves, HARCA, patch processes)? (y/N)"* at `SKILL.md:105`.

The prompt is removed entirely. Setup scaffolds an empty vault and finishes.
Seeding becomes a deliberate `brain_cli.py seed_index <path>` run when a pack
actually exists. This matches the requirement that seeding not matter at
install time.

### `ingest.py` rename (no behavior change)

The `seed_wins` branch at `ingest.py:619` reads `pages.pinned`:

```python
seed_wins = bool(conn.execute(
    "SELECT pinned FROM pages WHERE id=?", (old_id,)
).fetchone()[0])
```

This is a generic *"a pinned page loses to new user content"* rule that was
named after its first use case. Rename `seed_wins` → `pinned_wins`, update
the warning string `"seed override"` → `"pinned override"`, and reword the
comment. Logic untouched.

### e2e scripts

`scripts/e2e_seed_index.sh` and `scripts/e2e_seed_guards.sh` depend on
`seed/fpa106/` existing. Repoint both at a small fixture pack under
`tests/fixtures/seed-pack/` — a handful of neutral pages with baked ULIDs,
enough to exercise idempotency, the pinned exemption, and the prune guard
without shipping 137 pages.

---

## 4. De-corporatization

The project is named **Hera**, matching its directory. `BRAIN_OWNER` stays
`randy` — already correct.

### Audit command

Word boundaries are required. Without them, `sap` matches `di`**`sap`**`peared`,
which produced four false positives in the first pass:

```
git ls-files -z | xargs -0 grep -linE \
  "\b(sap|fpa106|harca|qrc|i771473|wdf|successfactors)\b|magnum opus|s-4hana"
```

Currently returns **10 files** outside `seed/` (excluding this spec). Must
return empty when done.

`docs/PIPELINES.md`, `docs/DATA-MODEL.md`, `docs/RETRIEVAL.md`, and
`scripts/team_index.py` contain **no** corporate references — each matched
only on the word "disappeared". `docs/PIPELINES.md` is still in the change set
below, but for the `--bare` invariant (§2) alone.

Two files in the change set below are **not** among those 14, and criterion 5
therefore does not cover them:

- `docs/ARCHITECTURE.md` — contains zero corporate references. It is in the
  change set only for the `--bare` invariant (§2).
- `.claude/settings.local.json` — untracked, so `git ls-files` never sees it.
  Its `/Users/I771473/` paths must be fixed and verified by hand.

### Change set

| File | Change |
|---|---|
| `README.md` | Clone URL `github.wdf.sap.corp/I771473/second-brain` → `https://github.com/randyren278/hera` (assumed from the repo's git identity; confirm before committing); title → Hera; delete the fpa106 seed paragraph (lines ~161–164) |
| `docs/README.md` | Lines 65, 68 — the seed-pack description (`seed/fpa106/`, "~137 fpa106 SAP") |
| `docs/PIPELINES.md` | `--bare` claims only (see §2); no corporate references present |
| `docs/DECISIONS.md` | NEVER-5 rewrite; line 117 `fpa106-team-brain` remote name; line 119 `I771473/.gitkeep` orphan-folder example reworded without the corporate ID |
| `docs/GLOBAL_INSTALL.md` | Line 78 — "magnum opus" → Hera (source-vs-clone discussion) |
| `docs/ARCHITECTURE.md` | `--bare` claims only (see §2); no corporate references present |
| `scripts/seed_index.py` | Lines 3, 191 — `seed/fpa106` used as the example pack path in docstring and `--help` |
| `scripts/e2e_seed_index.sh`, `scripts/e2e_seed_guards.sh` | Repointed at fixture pack (see §3) |
| `scripts/build_seed_pack.py` | Deleted (see §3) |
| `tests/test_hookcmd.py` | `/Users/dev/magnum opus` → `/Users/dev/hera vault`; keeps the deliberate space in the path |
| `.claude/skills/brain-setup/SKILL.md` | Seed prompt removed; SAP text gone |
| `.claude/settings.local.json` | `/Users/I771473/.claude/**` → `/Users/randyren/.claude/**`; untracked, verify by hand |

### Docs scope is smaller than first assessed

The initial audit suggested fpa106 pages were used as worked retrieval and
ranking examples throughout `docs/`. They are not. Every corporate reference
in `docs/` is a passing mention — a pack path, a remote name, a username, a
codename — and none is load-bearing to an explanation. The doc work is
therefore substitution, not rewriting.

The one exception is `README.md` line 2, which references
`public/orca.png` with alt text "Second Brain orca". "Orca" is the corporate
project codename (it appears throughout the seed pack as `[[Orca]]`). The
image file is neutral artwork and is kept; the alt text and any prose
reference change to Hera.

`scripts/check_docs_consistency.sh`, `scripts/check_doc_links.sh`, and
`scripts/check_mermaid_fences.sh` gate this. Note that
`check_docs_consistency.sh` asserts specific strings are *present* in
`README.md`, `docs/PIPELINES.md`, and `docs/ARCHITECTURE.md`
(`team_index.py`, `team.db`, `ADR-14`); edits must not remove them.

---

## 5. Environment rebuild and data wipe

Every path below is already gitignored (`.gitignore` lines 2, 8–14), so this
is local-only with zero git churn.

### Rebuild

- Delete and recreate `.venv`. Its `pyvenv.cfg` still reads
  `command = ... -m venv /Users/I771473/Desktop/magnum opus/.venv`, and
  `sqlite_vec/vec0.dylib` is missing from the copied package — the direct
  cause of all 17 test errors (`sqlite3.OperationalError: dlopen(... vec0.dylib) ... no such file`)
  and, via the preflight check, all 10 install-test failures.
- Reinstall `PIP_DEPS = ["sqlite-vec", "ulid-py", "requests", "pyyaml", "pytest"]`.
- Delete every `__pycache__` (repo root, `tests/`, `scripts/`,
  `scripts/install/`, `.claude/hooks/`), `.pytest_cache/`, and
  `.checkpoints/state.json`. Stale bytecode is leaking `/Users/I771473/...`
  paths into live test tracebacks.

### Wipe

- `brain.db`, `brain.db-wal`, `brain.db-shm`
- `team.db`, `team.db-wal`, `team.db-shm`
- `.brain/session-*.md`, `.brain/scorer.log`, `.brain/filing.log`
- `wiki/.raw/articles/session-*.md`
- `wiki/.archive/*`
- Reset `wiki/hot.md`, `wiki/index.md`, `wiki/log.md`, `wiki/overview.md` to
  empty scaffolding

**Keep** `wiki/meta/r1-verdict.md`. That is a design record — why Tier-1
citation scoring is disabled — referenced from `CLAUDE.md`, not work content.

### Regenerate

Empty `brain.db` via `brain_db.ensure_ready()`. Verify with
`brain_db.py --doctor`.

### Backup

Copy `brain.db` and `team.db` to the session scratchpad before deleting.
They are gitignored, so there is no git undo; the copies make the wipe
recoverable for the duration of this session.

---

## 6. Success criteria

1. `.venv/bin/python -m pytest tests/ -q` → **0 failed, 0 errors**
   (baseline: 10 failed, 71 passed, 17 errors)
2. Every `scripts/e2e_*.sh` and `tests/check_*.sh` exits 0
3. `.venv/bin/python scripts/brain_db.py --doctor` clean
4. **A real `/brain-ingest` of a fresh file completes end-to-end on the
   subscription** — creates source/concept/entity pages with ULIDs, updates
   `hot.md`/`index.md`/`log.md`, and refreshes FTS + vector rows. This is the
   criterion that proves the reported bug is actually fixed; the others are
   necessary but not sufficient.
5. The §4 audit grep returns empty
6. `/brain-setup` on a scratch vault completes without prompting about seed
   data

---

## 7. Risks

**The 10 install/skill/uninstall test failures are diagnosed, not confirmed.**
They trace to `install: preflight failed`, which is the `sqlite-vec-loads`
check at `install.py:697` — consistent with the broken venv. This has not
been proven for all 10. If any survive the rebuild, they are pre-existing
bugs outside this scope; they get surfaced and scoped separately rather than
absorbed silently.

**Wiping `brain.db` is irreversible.** Mitigated by the scratchpad backup
in §5.

**Doc example rewrites risk introducing false statements.** Mitigated by the
three existing docs-check scripts, which are part of criterion 2.

**Dropping `--bare` re-enables CLAUDE.md auto-discovery.** Mitigated by
`cwd=<tempdir>`. If a future change causes nested calls to run inside the
vault, the vault's own CLAUDE.md would enter the extraction prompt as noise.
The isolation test asserts on `cwd` to catch this.
