---
name: hera-setup
description: One-shot installer for a fresh clone of the Hera repo — scaffolds wiki/, creates hera.db from the §5 schema, verifies Ollama + nomic-embed-text, wires the hooks (§8), and (CP-7 onward) clones the team space staging repo. Also exposes `--doctor` for health checks.
---

# hera-setup

Clone-and-go installer for the Hera. Run once after cloning the repo.

> **Vault location.** This skill requires the vault root, written by
> `install.py` into `~/.claude/hera.env` as `HERA_VAULT`.
> If unset, error: `HERA_VAULT is not set — run python install.py from
> the vault directory first.`

**Invocation convention (OS-neutral).** Every engine call uses the one launcher
`scripts/hera_cli.py`, which self-locates the vault, resolves the venv
interpreter for the running OS, and re-execs the engine. Run it as:

    python "<VAULT>/scripts/hera_cli.py" <engine> [args...]

replacing `<VAULT>` with the absolute path from `HERA_VAULT` (the one-line
locator `~/.claude/hera.env`). This skill is the installer, so on a
*fresh* clone the locator may not exist yet — read it if present, otherwise run
from the vault directory you cloned; `python install.py` creates the locator.

## Steps

1. **Environment preflight.** Run `python "<VAULT>/scripts/hera_cli.py" preflight`.
   It checks a `.venv/` with `enable_load_extension`, the Python deps
   (`sqlite-vec`, `ulid-py`, `requests`, `pyyaml`, `pytest`), and pings Ollama.
   (`python install.py` bootstraps the `.venv` itself; this only verifies it.)

2. **Vault scaffold.** Create `wiki/` per design §3 with starter `hot.md`,
   `index.md`, `overview.md`, `log.md`, and the sub-folders `concepts/`,
   `entities/`, `sources/`, `questions/`, `.raw/articles/`, `.raw/images/`,
   `.pending/`, `.archive/`. The ingest engine creates these lazily on first
   write; this step is idempotent scaffolding for the human.

3. **Database.** `python "<VAULT>/scripts/hera_cli.py" hera_db --init`
   creates `hera.db` with the §5 schema and default config keys.

4. **Ollama + model.** Verify Ollama is running (`ollama serve` in background)
   and pull `nomic-embed-text` if missing.

5. **Hooks (CP-3).** Merge — **never clobber** — the SessionStart, UserPromptSubmit,
   Stop, and SessionEnd hook entries into `.claude/settings.json`. The plan's
   own checkpoint-runner Stop hook is already present; the merge must preserve
   it. This step is TODO until CP-3. `# CP-3`

6. **team space staging (optional).** Ask the user: **"Set up a team space? (y/N)"**
   A team space lets you publish redacted pages to — and search — a shared
   team space git repo. It is entirely optional; the single-user vault works
   fully without one.

   - **If no:** state that team features stay dormant. `/hera-team` commands
     will cleanly no-op with `No team space configured — run /hera-setup to
     add one.` until a remote is configured. Skip the rest of this step.

   - **If yes:** prompt for the team space git repo URL, then verify access
     *before* wiring anything (read-only — never clones on a bad URL):

     ```
     python "<VAULT>/scripts/hera_cli.py" team_sync check "<url>"
     ```

     `check` runs `git ls-remote` and exits 0 if the repo is reachable and
     your git auth resolves (an empty repo with no refs still counts as
     reachable). On failure, show the error and re-prompt for a URL, or let
     the user skip team setup. On success, persist the remote per-machine by
     appending (or replacing) this line in `~/.claude/hera.env`,
     beside the existing `HERA_VAULT` (plain `KEY=VALUE`, no `export`):

     ```
     HERA_TEAM_REMOTE="<url>"
     ```

     Then bring the shared team space to disk and ensure the owner's folder exists.
     `team_sync.py` reads the remote from `HERA_TEAM_REMOTE`; it
     clones when absent, initializes-over-existing when the folder is present
     but not yet a repo, and ff-pulls otherwise (idempotent):

     ```
     python "<VAULT>/scripts/hera_cli.py" team_sync clone-or-pull
     ```

     Then create the owner's staging folder
     `<VAULT>/team-staging/<owner>/` (owner = `HERA_OWNER`, default
     `randy`).

     The owner folder is named from `HERA_OWNER` (default `randy`) — the
     **same** identity every writer uses (`publish.py`, `team_remove.py`).
     Do not derive it from the git author name: that produced an orphan
     folder no writer touches. `team_sync.py clone-or-pull` also drops a
     `.gitignore` into the staging clone so OS junk (`.DS_Store`) never gets
     swept into a publish by the engines' `git add -A`.

   `/hera-team add` stages redacted pages under
   `team-staging/<owner>/` and requires human diff review + explicit
   approval before any push (ADR-08). `/hera-team retrieve` uses the same
   clone to pull *other* owners' pages down and search them. The remote is
   whatever URL you configured here — this vault is not tied to any fixed
   team space. Re-run `/hera-setup` any time to add or change it.

7. **Global CLAUDE.md (optional).** `install.py` step 7 offers to append the
   vault's `CLAUDE.md` into `~/.claude/CLAUDE.md` so Hera's *behavioral*
   rules (cite what you use as `(Source: [[Title]])`, never auto-push to
   team space, use the engines) stay active in **every** directory — not just
   the vault. Without it, querying from another project still injects pointers
   (the hooks are global), but final answers stop emitting citations, so the
   Stop hook records nothing and ranking quietly stops improving.

   It is **off by default**: an interactive install asks with a default of No;
   a non-interactive install opts in with `python install.py --with-global-claudemd`.
   The content is appended inside a marked block — pre-existing global
   CLAUDE.md content is preserved, and `python install.py --uninstall` removes the
   block cleanly. See `docs/GLOBAL_INSTALL.md` → "Optional: the global
   CLAUDE.md block" for the full contract.

## --doctor

`python "<VAULT>/scripts/hera_cli.py" hera_db --doctor`
runs every readiness check: preflight, DB schema, integrity, sqlite-vec load,
Ollama reachability, stale locks, unmerged deltas, scorer.log freshness, hook
registration.

Exit 0 means the vault is healthy.

## First-run UX

At the end of scaffolding, ask the user: "In one sentence, what is this vault
for?" and write the answer into `$HERA_VAULT/wiki/overview.md`. This
anchors every later extraction. State the opinionated defaults (points_final=5,
prune_pct 40-70, inject_top_n=3) explicitly rather than pretending neutrality.
