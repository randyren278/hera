---
name: brain-prune
description: Review pruning candidates and archive the middle-band pages of the vault's citation-point spectrum. Use when the user says "prune the vault", "clean up the wiki", "/brain-prune", or "show pruning candidates". Reversible — nothing is destroyed, only moved to wiki/.archive/.
---

# brain-prune

Manually triggered. Recommended monthly. Design §9.5 + ADR-07.

> **Vault location.** This skill requires the vault root, written by
> `install.py` into `~/.claude/second-brain.env` as `SECOND_BRAIN_VAULT`.
> If unset, error: `SECOND_BRAIN_VAULT is not set — run python install.py from
> the vault directory first.`

**Invocation convention (OS-neutral).** Every engine call uses the one launcher
`scripts/brain_cli.py`, which self-locates the vault, resolves the venv
interpreter for the running OS, and re-execs the engine. Run it as:

    python "<VAULT>/scripts/brain_cli.py" <engine> [args...]

replacing `<VAULT>` with the absolute path from `SECOND_BRAIN_VAULT` (the one-line
locator `~/.claude/second-brain.env`). Works identically on Windows and POSIX.

## What gets pruned

**Eligibility:**
- `type IN ('concept','entity','question')` — sources, domain pages, meta pages
  are NEVER pruned.
- age ≥ 30 days (configurable via `config.prune_min_age_days`) — young pages
  haven't had time to earn citations.
- not already archived.

**Selection:** eligible pages ranked by total citation points, ASC. The
middle band (default 40th–70th percentile — `config.prune_pct_low` /
`prune_pct_high`) is the prune zone. Keep the top (working context) and the
bottom (rarely-used-but-important).

## Flow

1. `python "<VAULT>/scripts/brain_cli.py" prune candidates` — dry-run listing.
2. Present the candidate list to the user for approval.
3. On approval: `python "<VAULT>/scripts/brain_cli.py" prune apply --yes` — moves the
   files under `<VAULT>/wiki/.archive/`, stamps `pages.archived_at`,
   clears the FTS and vector rows, and appends an entry to `wiki/log.md`.
4. On rejection: nothing changes.

## Restoring

`python "<VAULT>/scripts/brain_cli.py" prune restore <page-id>`
moves the file back to its original path, clears `archived_at`, and rebuilds
the FTS + vector rows so the page is retrievable again.

## Related

- `scripts/prune.py` — the ranking / move / index-clean logic
- `config.prune_min_age_days`, `config.prune_pct_low`, `config.prune_pct_high`
- `wiki/.archive/` — where pruned pages live; nothing is deleted
