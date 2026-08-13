# Seed packs

A **seed pack** is an optional, pre-built set of knowledge pages you can load
into a fresh vault in one shot. No pack ships with Hera — this directory
documents the format so you can build your own.

Loading a pack is always a deliberate act:

```
python scripts/brain_cli.py seed_index <pack_dir>
```

It writes only to the personal `brain.db` — never `team.db`.

## Pack format

```
<pack_dir>/
  concepts/*.md
  entities/*.md
```

**Concept and entity pages only.** No `sources/` — a pack carries distilled
knowledge, not source provenance.

**Filename must match the title.** A page's filename must equal
`ingest._slugify(title) + ".md"` — `_slugify` preserves case and spaces
(Obsidian convention), so a page titled `Spaced Repetition` must be the file
`Spaced Repetition.md`, not `spaced-repetition.md`. This matters because
`ingest._existing_page_at` keys on path: a pack using kebab-case filenames
indexes fine on its own, but a later ingest of the same concept then writes a
*second* page at the correctly-slugified path with a fresh ULID — no
contradiction check, no pinned-wins, just a duplicated page in search
results. See `tests/fixtures/seed-pack/` for a worked example of correctly
named pages.

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

- **Exempt from `/brain-prune`.** Pinned pages never enter the candidate band.
- **User content always wins.** A later note that contradicts a pinned page
  auto-resolves user-wins rather than freezing an open conflict, so a pack
  never blocks you with a conflict queue.

## Testing a pack

`tests/fixtures/seed-pack/` is a 4-page reference pack used by
`scripts/e2e_seed_index.sh` and `scripts/e2e_seed_guards.sh`. Copy its shape.
