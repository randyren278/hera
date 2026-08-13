<p align="center">
  <img src="public/orca.png" width="160" alt="Hera">
</p>

<h1 align="center">Hera</h1>

<p align="center">
  <em>A note vault your Claude Code sessions actually <b>use</b>, not an archive you re-read.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="python">
  <img src="https://img.shields.io/badge/SQLite-FTS5%20+%20vec-003B57?logo=sqlite&logoColor=white" alt="sqlite">
  <img src="https://img.shields.io/badge/embeddings-Ollama-000000" alt="ollama">
  <img src="https://img.shields.io/badge/runs%20in-Claude%20Code-D97757" alt="claude code">
  <img src="https://img.shields.io/badge/license-yours-2ea043" alt="license">
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#daily-use">Daily use</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a>
</p>

A note-taking system your Claude Code sessions **actually use**, not an
archive you mean to re-read and never do. Every session with Claude is
ingested automatically. On your next session, the pages that matter are
pulled into context *before* Claude answers. Notes that get cited get kept;
notes that never earn a citation get pruned. The vault teaches itself what's
worth keeping.

The vault is plain Markdown (Obsidian-compatible). The index is a single SQLite file: FTS5 for keyword
search and [`sqlite-vec`](https://github.com/asg017/sqlite-vec) for dense
embeddings, fused with Reciprocal Rank Fusion. Embeddings come from a local
[Ollama](https://ollama.com/) daemon running `nomic-embed-text`.

No hosted service sees your notes. The only
network calls are to the Claude API you're already using and, if you opt in,
your team's private GitHub repo.

---

## What it does

- **Ingest.** `/brain-ingest <path>` reads an article, transcript, or note,
  extracts atomic pages (concepts, entities, questions), assigns stable
  IDs, and files them under `wiki/`. Contradictions with existing pages are
  detected and surfaced as conflicts.
- **Recall.** Every prompt you send triggers a hybrid search over the vault.
  Top-N relevant pages are injected as pointers before Claude sees your
  question — and if a team brain is configured, matching team pages are fused
  in on the same scale, tagged with who published them. If a retrieved page is
  contested, the injection flags it.
- **Score.** When Claude finishes a turn, every `[[wikilink]]` and
  `(Source: [[Title]])` in the final answer becomes a citation record.
  Frequently-cited pages float; unused pages sink.
- **File sessions.** When your session ends, the transcript is distilled
  and ingested as a new source, closing the loop.
- **Resolve conflicts.** `/brain-conflicts` walks you through unresolved
  contradictions surfaced by ingest.
- **Publish.** `/brain-team add` runs a private ingest, then strips
  each page into a public-safe form and stages it under
  `team-brain-staging/<owner>/`. You review the diff and approve before
  anything is pushed to the shared team-brain repo.
- **Prune.** `/brain-prune` archives the middle band of your citation
  distribution (default 40th to 70th percentile, age at least 30 days). Top
  pages (working context) and bottom pages (rare but important) are preserved.
  Nothing is deleted. Everything goes to `wiki/.archive/` and stays
  restorable.

## What it doesn't do

- No hosted vector service, no cloud sync, no analytics.
- No auto-push to any remote. Public pages are staged; you approve every
  push by hand.
- No behind-your-back "quality improvements." Pages get archived by the
  prune command only, and only on explicit approval.

---

## Install

**Prerequisites:**
- **Python 3.11+ from [python.org](https://www.python.org/downloads/)** — this
  is the *one* thing you must install yourself. `install.py` provisions Ollama
  and the embedding model for you (see below).
  - It must have `sqlite3.Connection.enable_load_extension` (the python.org
    Windows/Linux builds and Homebrew macOS have it). **On Windows, don't use
    the Microsoft Store Python** — that build ships without extension loading
    and the vault's `sqlite-vec` index won't load. The macOS **python.org**
    build also lacks it; use Homebrew there.
- Claude Code 2.1+ (this vault is designed to run *inside* Claude Code)
- Windows 10/11, macOS, or Linux (native — no WSL/Git-Bash required)

You do **not** need to install Ollama first. `install.py` detects a missing or
stopped Ollama, offers to install it (defaulting to yes), starts the daemon,
and pulls `nomic-embed-text` — all in the one command below.

**One-time setup:**

```bash
# 1. Get the repo onto the machine (clone your own remote, or copy the
#    directory across — this vault is personal, not a published template).
cd ~/hera

# 2. Bootstrap the machine: venv, Ollama (install + start + model),
#    brain.db, and the global hooks + skills. On a fresh machine this
#    prompts "install Ollama now? [Y/n]" — press Enter to accept.
python install.py

# 3. Launch Claude Code from ANY directory and finish scaffolding.
#    Then, inside Claude Code, run: /brain-setup
claude
```

Ollama flags for `install.py`: pass `--install-ollama` to install/start it
without prompting (useful in scripts or when running non-interactively), or
`--no-install-ollama` to skip provisioning and only be told the exact command
to run yourself. With no flag, an interactive run asks (default yes) and a
non-interactive run proceeds.


The `python install.py` step turns the second brain into a **global**
capability. It **copies** the `brain-*` skills into `~/.claude/skills/`
(static markdown — a copy needs no admin/Developer-Mode on Windows and
creates no symlink), registers the four hooks by writing **absolute,
in-repo command strings** into your user-level `~/.claude/settings.json`
(preserving anything already there, with a timestamped backup), and writes
`~/.claude/second-brain.env` so the hooks and skills can locate the vault via
`SECOND_BRAIN_VAULT`. **No symlinks are created** — the hook command strings
point straight at the vault's `.claude/hooks/` scripts, so an edit to a hook
takes effect immediately with no mirror to refresh.

After that, launching `claude` from any directory injects vault context,
ingests the session on exit, and lets you invoke `/brain-ingest`,
`/brain-conflicts`, `/brain-prune`, and the rest from anywhere. The vault's own
project-local `.claude/settings.json` is renamed to `.disabled` to
prevent double-firing when you happen to `cd` into the vault.

`install.py` also **optionally** appends the vault's `CLAUDE.md` into your
user-level `~/.claude/CLAUDE.md`, so the brain's behavioral rules (cite what
you use, never auto-push to team-brain) stay active in *every* directory, not
just the vault. The tracked `CLAUDE.md` is the consumer-facing version — no
repo-role banner — so it's correct both at a clone's root and in your global
config. It's off by default: an interactive
install asks (default No), or pass `--with-global-claudemd` to opt in
non-interactively. It appends inside a marked block (your existing global
CLAUDE.md is preserved) and `--uninstall` removes it. See
[GLOBAL_INSTALL.md](docs/GLOBAL_INSTALL.md) → "Optional: the global
CLAUDE.md block".

`brain-setup` then:
1. Verifies Ollama is running and pulls `nomic-embed-text` if missing
2. Confirms the vault scaffold + `brain.db` are in place
3. Optionally sets up a team space — asks whether you want one, and if so
   prompts for a team-brain git repo URL, verifies access (`git ls-remote`),
   saves it to `~/.claude/second-brain.env` as `SECOND_BRAIN_TEAM_REMOTE`,
   and clones it into `team-brain-staging/`. Decline and team features stay
   dormant until you re-run setup.
4. Asks you what the vault is for and writes it into `wiki/overview.md`

**Health check anytime:** `python "<VAULT>/scripts/brain_cli.py" brain_db --doctor`

## Uninstall

Run it from the repo you cloned into (the installer finds itself, so you don't
need `SECOND_BRAIN_VAULT` set, and it won't be in a fresh shell).

```bash
# 1. From the cloned repo, preview exactly what will be removed.
cd ~/vault            # wherever you cloned it
python install.py --uninstall --dry-run

# 2. Happy with the preview? Run it for real.
python install.py --uninstall
```

It removes the copied `brain-*` skills it created (tracked in a manifest so it
only ever deletes its own), restores `~/.claude/settings.json` from the
backup taken at install time (or strips just our hook entries if no backup
existed), deletes `~/.claude/second-brain.env`, re-enables the vault's
project-local `.claude/settings.json`, and strips the optional second-brain
block from `~/.claude/CLAUDE.md` if it was added. It's safe by construction:
the uninstaller reverses everything **by content** (never by `readlink`), and
refuses to remove a skill dir it didn't create. Your `wiki/`, `brain.db`, and
the repo itself are never touched.

## How it works globally

- **Hooks** live in the vault (`.claude/hooks/`) and run **in place** — no
  `~/.claude/hooks/` mirror. `install.py` writes an absolute command string
  into `~/.claude/settings.json` that invokes the vault's venv interpreter
  against the in-repo hook script (`.venv\Scripts\python.exe` on Windows,
  `.venv/bin/python` elsewhere). Because the hook runs from its real path,
  `pathlib.Path(__file__).resolve()` yields the vault root directly.
  `SECOND_BRAIN_VAULT` overrides this if set, letting you point one machine at
  a different vault by editing `~/.claude/second-brain.env`.
- **Skills** are **copied** into `~/.claude/skills/` (not symlinked). Every
  engine command they name goes through `scripts/brain_cli.py`, which resolves
  the per-OS venv interpreter and re-execs the engine — one invocation shape on
  every OS.
- **Runtime state** (`brain.db`, `wiki/.pending/`, `.brain/`) still
  lives inside the vault. You can rename or move the vault by editing
  `~/.claude/second-brain.env`, and no other paths need to change.
- For deeper detail see [`docs/GLOBAL_INSTALL.md`](docs/GLOBAL_INSTALL.md).

---

## Daily use

Once installed, everything runs automatically:

- **Start a Claude Code session.** `wiki/hot.md` and any origin-scoped
  open conflicts are injected into context.
- **Ask a coding question.** Hybrid search finds relevant pages, and pointers
  are injected before Claude answers.
- **Claude cites a page.** The citation is recorded on Stop.
- **Session ends.** The transcript is distilled and ingested as a new
  source page.

Explicit commands you'll actually type:

| Command | What it does |
|---|---|
| `/brain-setup` | One-shot install (run once per machine) |
| `/brain-ingest <path>` | Ingest an article/transcript/notes file into the vault |
| `/brain-conflicts` | Walk through unresolved contradictions |
| `/brain-prune` | Archive the middle band; nothing destructive |
| `/brain-team add\|remove\|retrieve <...>` | All team-brain ops: publish your pages up (`add` runs a full private ingest, then strips and stages the public copy), un-publish your own (owner-scoped, human-gated delete), or search the team brain + your vault |

---

## Layout

```
wiki/                       ← your notes (Markdown, Obsidian-compatible)
  hot.md                    ← recent-context cache (rewritten every session)
  index.md                  ← one line per page
  log.md                    ← append-only ingest/prune/publish history
  overview.md               ← what this vault is for (anchors every extraction)
  concepts/                 ← "what" pages
  entities/                 ← "who/what-thing" pages (people, products, orgs)
  sources/                  ← ingested articles + session distillates
  questions/                ← open questions
  meta/                     ← design decisions (e.g. r1-verdict.md)
  .raw/articles/            ← original inputs (kept for provenance)
  .pending/                 ← delta overflow when a page is locked
  .archive/                 ← pruned pages (restorable)

scripts/                    ← engine (Python)
  brain_db.py               ← schema + connect() + --doctor
  ingest.py                 ← extraction, contradiction detection, filing
  search.py                 ← hybrid BM25 + vector + RRF
  conflicts.py              ← four-outcome resolver
  publish.py                ← strip + stage + push gate
  prune.py                  ← rank + archive + restore
  embed.py                  ← Ollama nomic-embed-text client
  locks.py                  ← per-file locking + delta overflow
  team_sync.py              ← pull-only clone/fast-forward of the team repo
  team_index.py             ← index teammates' pages into team.db (BM25+vector)
  team_search.py            ← hybrid search over team.db + your vault, owner-tagged
  team_remove.py            ← list + un-publish your own team pages (git-gated)
  preflight.sh              ← .venv bootstrap + Ollama verify
  e2e_*.sh                  ← regression tests

.claude/
  settings.json             ← hook registration
  hooks/
    session_start.py        ← hot cache + conflict alerts
    prompt_inject.py        ← hybrid-search injection
    stop_score.py           ← citation scorer (async, tier-2 only)
    session_end_file.py     ← transcript filing (async, fire-and-forget)
  skills/                   ← the /brain-* slash commands

tests/                      ← pytest units + fixtures
docs/                       ← the documentation set (start at docs/README.md)
```

---

## Team brain

The team brain is **optional and configurable**. During `/brain-setup` you're
asked whether you want a team space; if you do, you supply a team-brain git
repo URL, setup verifies you can reach it, and stores it per-machine in
`~/.claude/second-brain.env` as `SECOND_BRAIN_TEAM_REMOTE`. This vault is not
tied to any fixed remote — decline at setup and the `/brain-team` commands
simply no-op until you configure one.

`/brain-team add <path>` will:
1. Run the normal private ingest (all pages default to `visibility: private`)
2. LLM-strip each page into a public-safe copy (no names, no subjective
   claims, no internal identifiers)
3. Stage the stripped copies under `team-brain-staging/<owner>/`
4. Show you the diff
5. **Wait for you to say push.** Nothing is committed or pushed automatically.

Pulling the shared brain back down is the other half of the loop:
`/brain-team retrieve <query>` fast-forward-pulls the team repo, re-indexes
teammates' published pages into a separate `team.db`, and runs the **same hybrid
search** (BM25 + dense vectors + RRF) you get locally — keyed by each page's
publisher ULID (the page's permanent id) — over that index, fused with your personal vault and tagged with
who published each hit. `--owner <name>` scopes a query to one teammate. Team
pages live in `team.db`, never in your personal `brain.db`, so team retrieval
surfaces everyone's pages without touching your own ranking; the pull never
pushes. Every prompt you send also folds these owner-tagged team hits into the
context injection, ranked against your local pages on the same scale.

When a project ends and you want your context out of the shared space,
`/brain-team remove` lists **your own** published pages (newest first, with the
date each was added), you pick which to pull back (by date, topic, or title,
conversationally), and it stages a real `git rm`, shows you the diff, and waits
for your explicit push. It is **owner-scoped**: it only ever touches
`team-brain-staging/<you>/`, never a teammate's folder. Git history is the undo.

## Configuration

Runtime knobs live in the `config` table of `brain.db`. Sensible defaults are
seeded on `--init`:

| Key | Default | What it does |
|---|---|---|
| `inject_relevance_floor` | `0.015` | Skip pages below this RRF score |
| `inject_top_n` | `3` | Max pages injected per prompt |
| `points_final` | `5` | Points per final-answer citation |
| `prune_min_age_days` | `30` | Ignore pages younger than this |
| `prune_pct_low` | `40` | Prune band lower percentile |
| `prune_pct_high` | `70` | Prune band upper percentile |

Change a value by opening a `brain_db.connect()` connection and writing to
the `config` table (this is the only sanctioned path, since a raw `sqlite3`
connection won't load `sqlite-vec`):

```bash
"$SECOND_BRAIN_VAULT/.venv/bin/python" - <<'PY'
from scripts.brain_db import connect
conn = connect()
conn.execute("UPDATE config SET value = ? WHERE key = ?", ("5", "inject_top_n"))
conn.commit()
PY
```

---

## Design references

- [`docs/`](docs/README.md): the full documentation set (onboarding, architecture,
  data model, hooks, pipelines, and the ADR/invariants log)
- [`docs/DECISIONS.md`](docs/DECISIONS.md): the ADRs and the "never do this" invariants
- `wiki/meta/r1-verdict.md`: why Tier-1 (thinking-block) scoring is off

## License

Your notes are yours.
