# CLAUDE.md — how to work inside this vault

This file is loaded automatically at the start of every Claude Code session
in this repo. It tells you what this system is, what state it maintains,
and how not to break it. **Read it in full before doing anything that
touches the vault.**

In plain terms: this repo is a personal knowledge base that Claude Code reads and writes *for you* as you work — it saves what you learn, surfaces the relevant bits back into your next prompt, and gets better at that the more you use it. Most of that happens automatically through four hooks and a handful of engines. The rules below exist so that automatic machinery is never corrupted, and so private notes never leak to a shared team space. When in doubt, do less: surface a conflict, don't overwrite; ask, don't guess.

---

## What this repo is

A single-user "Hera" running inside Claude Code. It's a Markdown
vault (`wiki/`), a SQLite index (`hera.db`), a set of Python engines
(`scripts/`), and four Claude Code hooks (`.claude/hooks/`) that stitch
them together. Full documentation set: `docs/` (start at `docs/README.md`);
decisions and invariants in `docs/DECISIONS.md`.

**The user is the owner of these notes.** Every write to `wiki/` should be
traceable to either (a) the user asking for it or (b) an automatic engine
run (ingest, session filing, prune). Never write to `wiki/` "helpfully"
outside those paths.

---

## The four hooks (they run automatically — know what they do)

These four hooks read and write the search index around each turn so relevant past notes surface automatically. Two terms recur below: a **pointer line** is a one-line reference to a vault page (its title and path, not the body); **hybrid search** runs two rankers over your query — BM25 keyword match and dense-vector (semantic) similarity — then merges them with Reciprocal Rank Fusion (RRF), which combines by each hit's *rank position*, not its raw score, so the two incomparable scales fuse cleanly. (Fuller definition: [docs/ONBOARDING.md § Glossary](docs/ONBOARDING.md#glossary).)

| Hook | When | What it does |
|---|---|---|
| `session_start.py` | Session starts | Emits `hot.md`; warns about origin-scoped open conflicts and stale pending deltas |
| `prompt_inject.py` | User submits a prompt | If the prompt looks like a coding question, runs hybrid search over `hera.db` and injects top-N pointer lines; if a team space is configured, also runs hybrid search over `team.db` and fuses those owner-tagged (`team: <owner>`) pointers in by score. Fail-open: any error → no output. |
| `stop_score.py` | You finish a turn | Async. Scans your final answer for `[[wikilinks]]` and `(Source: [[Title]])`, records citations in `hera.db`. Never blocks. |
| `session_end_file.py` | Session ends | Async. Distills the transcript into `.hera/session-<id>.md` and ingests it as a `source_kind=session` page. |

**Consequence for you:** anything you cite as `[[Title]]` in a final answer
becomes a scored citation that shifts the vault's ranking. Cite things
that were actually useful. Do not sprinkle wikilinks decoratively.

**Where they live after global install.** The hooks run **in-repo** — there is
no `~/.claude/hooks/` mirror and no symlink anywhere. `install.py` writes an
absolute command string into `~/.claude/settings.json` pointing straight at the
vault:

```
"<vault>/.venv/bin/python" "<vault>/.claude/hooks/<hook>.py"
```

That string is generated per-OS by `scripts/install/hookcmd.py` and merged by
`scripts/install/settings.py` (which supersedes the stale
`scripts/install/settings_fragment.json`). It deliberately contains no `.`-source,
no `&&`, and no `$VAR`, because cmd.exe cannot run those.

Each hook's `REPO` variable prefers `$HERA_VAULT` (from
`~/.claude/hera.env`) and falls back to
`pathlib.Path(__file__).resolve().parents[2]` — with the in-repo path, `__file__`
is already correct. Edits to hook source in the vault take effect immediately —
no reinstall needed, the same benefit a symlink would have given.

Skills are **copied** into `~/.claude/skills/hera-*`, not linked; uninstall
reverses that by content via the `~/.claude/.hera-manifest` record. Rationale
in [docs/GLOBAL_INSTALL.md](docs/GLOBAL_INSTALL.md) §"Why copies + in-repo hooks,
not symlinks"; enforced by `tests/test_no_symlink.py`.

---

## The five skills the user will invoke

All live under `.claude/skills/`. Each has a `SKILL.md` — read it before
acting.

- `/hera-setup` — one-shot install. Run once per machine.
- `/hera-ingest <path>` — ingest a file into the vault. Runs extraction,
  contradiction detection, ULID assignment, per-file locked writes.
- `/hera-conflicts` — walk through unresolved contradictions from a
  channel (freeze-on-ingest, hot.md warning, session-start alert, or
  in-context injection warning).
- `/hera-prune` — dry-run first, then archive middle-band pages on
  approval. Only concept/entity/question pages ≥ 30 days old.
- `/hera-team add|remove|retrieve|pull` — all team space operations in one
  skill. **add**: LLM-strip + stage under `team-staging/<owner>/`, then
  human-gated push (this is the honest name for the old `-private`). **remove**:
  un-publish *your own* pages — `team_remove.py list` (owner-scoped, git-dated),
  you pick conversationally, then staged `git rm` + human-gated push; owner-scoped,
  git history is the undo. **retrieve**: answer "what does the team know about X"
  by searching teammates' published pages alongside your own. It first syncs
  (pull-only) so the local team index is current, then runs the same hybrid
  search you get locally (BM25 + dense + RRF) across both the team pages and your
  personal `wiki/`, tagging each hit with who owns it. Engines: `team_sync.py
  clone-or-pull` (which re-indexes teammates' pages into `team.db`) then
  `team_search.py`. **pull**: sync only. **Never push
  without explicit human approval.**

When the user says "prune the vault" / "clean up the wiki" / "show
candidates" → `/hera-prune`. When they say "add this to Hera" /
"remember this" / "ingest this" → `/hera-ingest`. When they say "share/publish
this to team" → `/hera-team add`; "remove/unpublish my team pages" →
`/hera-team remove`; "what does the team know about X" / "what did <teammate>
publish" / "search the team space" → `/hera-team retrieve`.

---

## What NEVER to do

1. **Never push to `team-staging/`'s remote automatically.** The
   remote is whatever the user configured at `/hera-setup`
   (`HERA_TEAM_REMOTE` in `~/.claude/hera.env`); there is no
   fixed remote. Only push after the user has reviewed the diff and explicitly
   said "push" / "publish" / "approve".
2. **Never edit `hera.db` by hand outside `scripts/`.** Use
   `scripts/hera_db.py` or the engines. If you must SQL, do it via
   `hera_db.connect()` so `sqlite-vec` is loaded.
3. **Never bypass locking.** All writes to `wiki/` go through
   `locks.lock()`. If a page is locked, the writer overflows to
   `wiki/.pending/`, which merges on next acquire. Don't work around this.
4. **Never delete from `wiki/.archive/`.** Prune is reversible by design;
   real deletion is a human decision only.
5. **Never invoke `claude -p ...` from inside an engine without the
   isolation flags — and never with `--bare`.** Nested Claude calls
   otherwise inherit this vault's hooks and recurse. Every subprocess call
   in `ingest.py` and `publish.py` passes `CLAUDE_ISOLATION`
   (`--setting-sources ""`, `--strict-mcp-config`, `--tools ""`,
   `--disable-slash-commands`) and runs with `cwd=CLAUDE_CWD`. `--bare` is
   **forbidden**: it skips keychain reads and authenticates only via
   `ANTHROPIC_API_KEY` or `apiKeyHelper`, so nested calls die with `Not logged in` on a
   subscription.
6. **Never disable a hook to "quiet things down".** If a hook is
   misbehaving, fix it or report it — don't silence the loop. (To
   intentionally opt out of the loop for a session, the user sets
   `HERA_OFF=1` — see [docs/HOOKS.md](docs/HOOKS.md) §2a. That is the
   sanctioned off switch; editing `settings.json` to mute a hook is not.)
7. **Never modify checks to make them pass.** (This vault was built under
   the Fable plan-verify-execute protocol; the same rule applies to any
   later verification work.)
8. **Never bypass `$HERA_VAULT`.** Hooks and skills locate the
   vault via that env var (written by `install.sh` to
   `~/.claude/hera.env`). Hard-coding a path breaks global mode
   and quietly points one machine at the wrong vault.
9. **Never let team content into personal `hera.db`.** Team pages are
   indexed only in the separate `team.db` (ADR-14); `team_index.py` opens
   `TEAM_DB`, the personal engines open `hera.db`. Don't merge the stores
   or point a team writer at `hera.db` — it pollutes your local ranking,
   citations, and conflicts with other people's notes.

---

## What TO do

- **Cite what you use.** If a fact came from `[[Retrieval-Augmented
  Generation]]`, say `(Source: [[Retrieval-Augmented Generation]])` in
  your final answer. That's how the vault learns which pages matter.
- **Surface conflicts in-context.** If the injected context says
  `⚠ contested — existing: X · new: Y · unresolved.`, raise it with
  the user rather than picking a side silently.
- **Use the engines, not shell tricks.**
  - Ingest a file → `"$HERA_VAULT/.venv/bin/python" "$HERA_VAULT/scripts/ingest.py" <path>`
  - Search the vault → `from search import hybrid_search; hybrid_search(conn, q)`
  - Resolve a conflict → `"$HERA_VAULT/.venv/bin/python" "$HERA_VAULT/scripts/conflicts.py" resolve <id> new|old|both`
  - Health-check → `"$HERA_VAULT/.venv/bin/python" "$HERA_VAULT/scripts/hera_db.py" --doctor`
- **Match existing style when editing markdown pages** — frontmatter
  keys, callout syntax (`> [!info]`, `> [!source]`, `> [!conflict]`),
  wikilink format `[[Title]]`.
- **Ask before deleting or rewriting a page.** If a page seems wrong,
  raise the contradiction — don't overwrite. That's what the conflict
  pipeline is for.

---

## Design decisions you'll bump into

- **Tier-1 (thinking-block) citation scoring is disabled.** See
  `wiki/meta/r1-verdict.md`. Only final-answer wikilinks are counted.
- **Session filing is fire-and-forget on SessionEnd.** Idempotent via
  `filed_sessions` table. Safe to retry.
- **Contradictions freeze pending pages (ADR-09).** They surface via
  four channels (ADR-10). Session-source explicit user statements
  auto-resolve as `resolved_new` (ADR-11); everything else waits.
- **Ranking uses Reciprocal Rank Fusion (k=60)** over BM25 and dense
  cosine. Floor is `0.015` — recalibrate before changing.
- **ULIDs are page addresses.** Filenames may change, IDs don't.

---

## Where to look when things break

- `.hera/scorer.log` — Stop-hook scoring output
- `.hera/filing.log` — SessionEnd filing output
- `wiki/.pending/*.delta.md` — unmerged writes waiting for a lock
- `scripts/hera_db.py --doctor` — one-shot readiness check
- `~/.claude/hera.env` — vault-locator (should contain
  `HERA_VAULT`)
- `~/.claude/settings.json` — the hook commands. Each should be an absolute
  `"<vault>/.venv/bin/python" "<vault>/.claude/hooks/<hook>.py"` pair. There is
  **no** `~/.claude/hooks/` directory — if you are looking for one, that is the
  bug in your mental model, not the install.
- `~/.claude/skills/hera-*` — real directories **copied** from the vault (not
  links), recorded in `~/.claude/.hera-manifest`
- `~/.claude/settings.json.hera-backup.*` — pre-install backups; use
  `install.sh --uninstall` to restore
- `docs/GLOBAL_INSTALL.md` — global-install architecture reference
- `docs/ARCHITECTURE.md` — system-level map and "where to look when it breaks"
- `docs/DECISIONS.md` — the ADRs and the "never do this" invariants

---

## Team space remote (configurable)

The team space remote is **not fixed**. `/hera-setup` asks whether you want a
team space; if so, you supply a git repo URL, setup verifies access and stores
it per-machine as `HERA_TEAM_REMOTE` in `~/.claude/hera.env`.
`team_sync.py` reads that variable; with no remote configured, every
`/hera-team` command is a clean no-op.

`/hera-setup` clones the configured remote into `team-staging/`.
`/hera-team add` stages redacted pages there under `<owner>/`. **Push
requires explicit human approval every time.**
