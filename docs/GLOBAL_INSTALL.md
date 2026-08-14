# Global install — architecture reference

**Why this exists:** by default the vault's hooks and skills only work when Claude Code is running *inside* the vault directory. "Global install" is the one-time step that makes them work in **any** directory — so Hera keeps surfacing notes and scoring citations while you work in unrelated projects — without copying the vault around. The trick is that everything is wired with an absolute path back to the single canonical vault on disk. This doc explains how that wiring works, why it's shaped this way, and where to look when it misbehaves; read it when you're installing, uninstalling, or debugging a hook that won't fire from a foreign directory.

`install.py` at the vault root converts a fresh clone into a globally
available Hera: hooks fire in every Claude Code session, the
`hera-*` skills are invokable from any directory, and everything
still points back at the one canonical vault on disk. It runs on Windows,
macOS, and Linux — no symlinks, no bash.

This doc explains the pieces, why each is shaped the way it is, and
where to look if something misbehaves.

## The four moving parts

1. **`~/.claude/hera.env`** — a single-line, shell-neutral file:
   ```
   HERA_VAULT="/absolute/path/to/vault"
   ```
   Plain `KEY=VALUE` with **no** `export` prefix, written by
   `scripts/install/locator.py`. It is **read by Python**, not sourced by a
   shell, so it works identically on cmd.exe/PowerShell and POSIX. Each hook
   resolves the vault from this file's env var *or* from its own on-disk
   location (see below), so it needs no shell to pre-export anything.

2. **In-repo hooks (no `~/.claude/hooks/` mirror).** The hooks run straight
   from `<vault>/.claude/hooks/*.py`. All four resolve their `REPO` like this:
   ```python
   _env_vault = os.environ.get("HERA_VAULT")
   REPO = pathlib.Path(_env_vault).resolve() if _env_vault else pathlib.Path(__file__).resolve().parents[2]
   ```
   The env-var path wins when present. The `__file__.resolve()` fallback works
   because the hook runs from its real in-repo path — `parents[2]` is the vault
   root. Editing hook source takes effect immediately; there is no mirror to
   refresh and no symlink to dangle.

3. **`~/.claude/skills/hera-*`** — real **copies** of the vault's
   `<vault>/.claude/skills/hera-*` directories (skills are static markdown; a
   copy needs no admin/Developer-Mode on Windows and creates no symlink).
   `install.py` tracks the dirs it created in a manifest
   (`~/.claude/.hera-manifest`) so uninstall removes exactly those and never a
   dir it didn't create. Each `SKILL.md` invokes engines through
   `scripts/hera_cli.py`, which resolves the per-OS venv interpreter, so a user
   in any CWD ends up talking to the same `.venv/` and the same `hera.db`.

4. **Merged `~/.claude/settings.json`** — `install.py` **generates** the hook
   fragment for the running OS (`scripts/install/hookcmd.py`) and merges it via
   `merge_settings` (`scripts/install/settings.py`). Each hook command is an
   absolute interpreter+script invocation pointing in-repo (no `.`-source, no
   `&&`, no `$VAR`). The merge deduplicates by command string, so re-running
   `install.py` is a no-op on already-installed machines.

## Why copies + in-repo hooks, not symlinks

- **Windows.** Symlink creation on Windows needs Developer Mode or elevation.
  A copied skill dir and an absolute in-repo hook command need neither.
- **Hooks stay live.** The hook command string points straight at the vault's
  `.claude/hooks/` script, so editing a hook reflects globally, immediately —
  the same benefit a symlink gave, without the symlink.
- **Skills are static.** A `SKILL.md` is markdown + assets with no live-edit
  requirement, so a copy is fine; the manifest makes uninstall exact.
- The uninstaller reverses everything **by content** (strip our settings
  entries, remove manifest-tracked skill dirs) — never by `readlink`.

## Backup + restore

`install.py` writes `~/.claude/settings.json.hera-backup.<timestamp>`
before the very first merge — see `backup_file()` in
`scripts/install/settings.py`. `--uninstall` restores the newest backup by
mtime and leaves everything else alone.

If no pre-install backup exists (meaning the machine had no user-level
`settings.json` before install), the uninstaller strips out only our
hook entries. If that empties the file, it's removed.

## Optional: the global CLAUDE.md block

**First, the source-vs-clone model this section rests on.** This repo (`hera`) is the *source/template* that others clone. A line like "this repo is the source, edit and push here" is true *here* but would be false in a clone — it would "invert," telling a consumer their read-only copy is the upstream. So that maintainer-only guidance lives in `MAINTAINERS.md`, which is git-ignored and never reaches a clone. What remains — the invariants and the "cite what you use" rule — is *consumer-facing* and correct everywhere, so the tracked `CLAUDE.md` already **is** the version safe to ship. With that in mind:

The retrieval half of Hera (hooks: session-start context, prompt
injection, citation scoring, session filing) works in **any** directory
once installed, because the hooks are wired with an absolute
`$HERA_VAULT` path. But the *behavioral* half — cite what you use
as `(Source: [[Title]])`, never auto-push to team space, use the engines
not shell tricks — lives in the vault's `CLAUDE.md`, which Claude Code
only loads when the session's project directory is the vault itself.

So outside the vault the loop still *runs* but stops *learning*: without
the citation rule loaded, final answers don't emit `(Source: [[…]])`, the
Stop hook finds nothing to score, and ranking quietly stops improving.

Step 7 of `install.py` offers to fix that by appending the vault's
`CLAUDE.md` into `~/.claude/CLAUDE.md` (the user-level file Claude Code
loads in *every* project). The tracked `CLAUDE.md` is already the
**consumer-facing** version — it carries the invariants and the citation
rule but **no repo-role banner** (that "this repo is the SOURCE/TEMPLATE,
edit + push here" guidance lives in `MAINTAINERS.md`, which is
git-ignored and source-repo-local — never committed, never cloned —
precisely because it would invert on a clone). So the file that ships to a
consumer's global config is correct by construction — there is no separate
mirror file and nothing to strip at install time. It is:

- **Off by default.** Interactive runs are asked with a default of No;
  it never touches the global file unless you say yes.
- **Non-interactive opt-in.** For scripted/CI installs with no TTY, pass
  `--with-global-claudemd` to append without a prompt. With no flag and
  no TTY, the step is skipped and prints how to add it later.
- **Append, never replace.** The content goes inside a marked block
  (`# >>> Hera (managed by install.py) >>>` …
  `# <<< Hera <<<`); any pre-existing global CLAUDE.md content is
  preserved byte-for-byte. `install.py` backs up the target first before
  writing.
- **Idempotent.** Re-running replaces the block in place (refreshing the
  body from the current `CLAUDE.md`) — never a second copy.
- **Reversible.** `install.py --uninstall` (step 5) strips the block,
  restoring surrounding content; if the block was the file's only content, the
  file is removed.

Because the tracked `CLAUDE.md` *is* the consumer version, a fresh
`git clone` also gets the correct, banner-free file at its root — and
gets **no** repo-role banner anywhere, since `MAINTAINERS.md` (the only
place the banner lives) is git-ignored and stays in the maintainer's
working copy alone.
A maintainer-only doc-CI script (`check_global_mirror.sh`, git-ignored and
source-repo-local like `MAINTAINERS.md`, so it is **not** present in a clone)
guards against a regression that reintroduces the banner into `CLAUDE.md`: it
asserts the produced mirror carries the invariants and contains no
`SOURCE / TEMPLATE` assertion.

Trade-off worth knowing: the vault `CLAUDE.md` then loads into every
session in every project, including unrelated ones — a token cost. If that
becomes annoying, `--uninstall` removes it, or trim `CLAUDE.md` to a
slimmer ruleset (which also slims what a clone gets at root).

## Why disable the project-local `.claude/settings.json`

Claude Code merges user-level and project-local hooks. If both fire, you
get:
- double citations recorded on Stop (both `stop_score.py` invocations
  run and each updates `points_final`)
- two SessionEnd filing jobs racing on the same session-id (idempotent
  via `filed_sessions`, but wasteful)
- inject content emitted twice per prompt

To sidestep all of it, `install.py` renames `<vault>/.claude/settings.json`
to `.disabled` after the first successful global install. `--uninstall`
puts it back.

## Hook command surface

Each generated hook command is a single interpreter+script invocation with
absolute paths and no shell operators. On POSIX:

```
"<vault>/.venv/bin/python" "<vault>/.claude/hooks/session_start.py"
```

On Windows:

```
"<vault>\.venv\Scripts\python.exe" "<vault>\.claude\hooks\session_start.py"
```

Three things worth knowing:

- **No env sourcing.** There is no `. "$HOME/…"` prefix and no `$VAR` — the
  hook resolves the vault from `HERA_VAULT` (if Claude Code exports it)
  or from its own in-repo `__file__`, which is already correct because the hook
  runs from its real path. cmd.exe can run the string verbatim.
- **Vault venv, not system Python.** The interpreter is the vault's `.venv/`
  (which has `sqlite-vec`, the HTTP client for the Ollama embedder, etc.).
- **In-repo path.** The script path points straight at
  `<vault>/.claude/hooks/`, so `__file__` resolves to the vault with no symlink
  to follow.

## Windows notes

- **Developer Mode is not required.** The installer creates no symlinks — skills
  are copied and hooks run in-repo — so no elevation or Developer Mode is needed.
- **Interpreter path.** The venv interpreter is `.venv\Scripts\python.exe`
  (POSIX is `.venv/bin/python`); `install.py`, `hera_cli.py`, and the generated
  hook commands all resolve this per-OS automatically.
- **Locator is read, not sourced.** `~/.claude/hera.env` is plain
  `KEY=VALUE` (no `export`), parsed by Python — nothing shell-specific.

## Multi-machine, multi-vault

- **One vault, many machines.** Just clone the vault and run
  `python install.py` on each. Runtime state (`hera.db`, `.hera/`, `wiki/`)
  is per-clone and not synced — that's a data-sync question, out of
  scope for the installer.
- **Different vault at a different location.** Edit
  `~/.claude/hera.env` to point elsewhere. All hooks and skills
  follow immediately. If you moved the clone, the generated hook command
  strings still carry the old absolute path — re-run
  `python install.py --uninstall && python <new-path>/install.py`.
- **Multiple vaults simultaneously.** Not supported. One
  `HERA_VAULT` per user. If you need this, either use two OS
  users or run the second Claude Code instance with a per-invocation
  `HERA_VAULT=<other>` override.

## Failure modes and where to look

| Symptom | Likely cause | Where to look |
|---|---|---|
| Hooks don't fire in a foreign CWD | `~/.claude/settings.json` merge lost or absent | `python -c "import json; print(json.load(open('$HOME/.claude/settings.json'))['hooks'].keys())"` |
| `HERA_VAULT` env var empty in a hook | locator missing or malformed | `type %USERPROFILE%\.claude\hera.env` (Windows) / `cat ~/.claude/hera.env` (POSIX) |
| Hook fires but resolves to the wrong vault | locator env var points elsewhere, or the generated command carries a stale absolute path after a move | edit `~/.claude/hera.env`, or re-run `python install.py` from the current location |
| Skill errors with "vault not set" | locator absent and the skill couldn't self-locate | re-run `python install.py`; confirm `~/.claude/hera.env` exists |
| Hook command points at an old path | vault was moved after install | re-run `python install.py --uninstall` then `python install.py` from the new location |
| Both project-local and global hooks firing | `<vault>/.claude/settings.json` wasn't disabled | check for `.disabled` sibling; if missing, rename it manually |

## Files, at a glance

```
<vault>/install.py                       # bootstrap + uninstall (cross-platform)
<vault>/install.sh                       # thin POSIX shim → install.py
<vault>/scripts/install/venv.py          # per-OS venv resolve + bootstrap
<vault>/scripts/install/locator.py       # writes ~/.claude/hera.env
<vault>/scripts/install/hookcmd.py       # per-OS hook command generation
<vault>/scripts/install/settings.py      # backup/restore/merge/strip helpers
<vault>/scripts/install/registration.py  # copy skills + manifest-based uninstall
<vault>/scripts/install/preflight.py     # OS-neutral env checks (doctor + install)
<vault>/scripts/hera_cli.py             # OS-neutral engine launcher (skills use it)
<vault>/scripts/install/check_skill_invocations.sh  # skill-portability verifier
<vault>/tests/test_locator_py.py         # CP-1 locator tests
<vault>/tests/test_venv_resolve.py       # CP-1 venv-resolve tests
<vault>/tests/test_hookcmd.py            # CP-2 hook-command tests
<vault>/tests/test_settings_merge.py     # CP-2 merge/strip tests
<vault>/tests/test_no_symlink.py         # CP-3 no-symlink proof
<vault>/tests/test_skill_registration.py # CP-3 skill-copy tests
<vault>/tests/test_uninstall_py.py       # CP-3 uninstall roundtrip
<vault>/tests/test_windows_branch_sim.py # CP-8 os.name-forced Windows sim
```

## When you change something

- **Adding a new skill?** Add its directory to `SKILL_DIRS` in
  `install.py` and rerun `python install.py` — the new dir is copied into
  `~/.claude/skills/` and tracked in the manifest.
- **Adding a new hook event?** Extend `_EVENT_META` in
  `scripts/install/settings.py` and `HOOK_SCRIPTS` in
  `scripts/install/hookcmd.py`. Rerun install; the merge is idempotent
  and won't duplicate existing entries.
- **Changing where the env file lives?** Update the locator path in
  `scripts/install/locator.py` — the hooks read the file via Python, so there
  is no second shell-side place to keep in sync.
