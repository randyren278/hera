---
name: brain-ingest
description: Ingest a source (URL, file, image, or a batch) into the Second Brain vault — creates source/concept/entity pages, updates hot/index/log, refreshes the FTS + vector index, and (from CP-5 onward) surfaces knowledge conflicts. Use when the user says "ingest this", "add this to the vault", pastes a URL, or points at a document to remember.
---

# brain-ingest

The slash command `/brain-ingest` handles all Single Source Ingest (design §9.1).
This SKILL is a thin dispatcher: it prepares the input and delegates the actual
work to `scripts/ingest.py`. Every write goes through the per-file locking
protocol implemented in `scripts/locks.py` (§6, ADR-02) — the ingest engine
imports `locks.py` directly, so nothing here needs to open lockfiles itself.

> **Vault location.** This skill requires the vault root, written by
> `install.py` into `~/.claude/second-brain.env` as `SECOND_BRAIN_VAULT`.
> If unset, error: `SECOND_BRAIN_VAULT is not set — run python install.py from
> the vault directory first.`

**Invocation convention (OS-neutral).** Every engine call in this skill uses the
one launcher `scripts/brain_cli.py`, which self-locates the vault, resolves the
venv interpreter for the running OS, and re-execs the engine. Run it as:

    python "<VAULT>/scripts/brain_cli.py" <engine> [args...]

replacing `<VAULT>` with the absolute path from `SECOND_BRAIN_VAULT` (read the
one-line locator `~/.claude/second-brain.env`). This works identically on
Windows (cmd.exe/PowerShell) and POSIX — no shell sourcing, no venv-path
hardcode.

## Triggers

- URL: any argument matching `^https?://`
- File path: any existing path ending in a text or markdown extension
- Image path: `.png .jpg .jpeg .gif .webp .svg .avif`
- Batch: multiple paths, or a directory

## Flow

1. **Discuss the source with the user briefly.** Ask "how granular?" and "any
   emphasis?" unless the user said "just ingest it" or the caller is a
   background job (SessionEnd filing).
2. **Fetch/clean.** For URLs, use WebFetch; if `defuddle` is installed (`which
   defuddle`), pipe through it. Save the cleaned raw source to
   `<VAULT>/wiki/.raw/articles/`. For images, extract description +
   OCR text natively and copy the image to `<VAULT>/wiki/.raw/images/`.
3. **Delegate to the engine.** Invoke
   `python "<VAULT>/scripts/brain_cli.py" ingest <path> --kind file|url|image --json`.
   This runs the extraction (an isolated `claude -p` call with a strict JSON schema),
   writes the source/concept/entity pages through the locking protocol in
   `scripts/locks.py`, upserts `pages`/`pages_fts`/`pages_vec` rows, and updates
   `wiki/hot.md`, `wiki/index.md`, `wiki/log.md`.
4. **Report.** Summarise what was created and updated. Cite the source
   wikilink. If the engine reported `warnings`, mention each one — from CP-5
   onward, those warnings become `conflicts` queue entries.

## Batch mode

Given multiple paths, run the engine once per path with cross-referencing
deferred. After the batch, run one pass that scans the new pages for wikilinks
to titles that now exist and rewrites them if needed. Update hot/index/log
**once** at the end.

## Contradiction handling

- **CP-2:** the extractor is instructed to *report* possible contradictions in
  `warnings`; the ingest engine does not create `conflicts` rows yet.
- **CP-5:** add the LLM contradiction-detection step and enqueue rows via
  `scripts/brain_db.py`.

## Related

- `scripts/ingest.py` — the ingest engine (page writes go through `locks.py`)
- `scripts/embed.py`, `scripts/search.py` — index refresh + hybrid retrieval
- `.claude/skills/brain-conflicts/SKILL.md` — conflict queue review (CP-5)
- `.claude/skills/brain-team/SKILL.md` — public/team publish flow (`add`)
