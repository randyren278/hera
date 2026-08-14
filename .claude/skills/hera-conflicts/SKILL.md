---
name: hera-conflicts
description: Review and resolve open knowledge conflicts in the vault. Use when the user says "show conflicts", "resolve conflicts", "/hera-conflicts", or the SessionStart hook surfaces open conflicts from the current project.
---

# hera-conflicts

Global queue review (design §7.3 channel 4). Lists every open conflict with:

- the affected page (wikilink)
- **existing** claim (currently on the page)
- **new** claim (from the source that triggered detection)
- the source of each claim, with dates
- origin project (cwd of the session that detected it)
- age

> **Vault location.** This skill requires the vault root, written by
> `install.py` into `~/.claude/hera.env` as `HERA_VAULT`.
> If unset, error: `HERA_VAULT is not set — run python install.py from
> the vault directory first.`

**Invocation convention (OS-neutral).** Every engine call uses the one launcher
`scripts/hera_cli.py`, which self-locates the vault, resolves the venv
interpreter for the running OS, and re-execs the engine. Run it as:

    python "<VAULT>/scripts/hera_cli.py" <engine> [args...]

replacing `<VAULT>` with the absolute path from `HERA_VAULT` (the one-line
locator `~/.claude/hera.env`). Works identically on Windows and POSIX.

## Actions per open conflict

- **keep new** → new claim wins, `resolved_new`. The page is updated: the new
  claim replaces the contradicting sentence, and an `## Superseded` section is
  appended recording the old claim, its source wikilink, and the date range it
  was believed. Nothing is destroyed.
- **keep old** → new claim rejected, `resolved_old`. Page unchanged. The queue
  row is preserved for auditability.
- **keep both** → both legitimate in different contexts, `resolved_both`. A
  permanent `> [!conflict]` callout is added to the page listing both claims
  with sources — the ONLY place that callout exists (design §7.4). Not a
  pending-state artifact.
- **dismiss** → false positive, `dismissed`. Page unchanged. Queue row kept.

## Flow

1. Query `python "<VAULT>/scripts/hera_cli.py" conflicts list`.
2. Present each conflict compactly and ask the user which action to take.
3. Invoke `python "<VAULT>/scripts/hera_cli.py" conflicts new|old|both|dismiss <cid>` per resolution.
4. All page writes route through `scripts/locks.py`.

## When conflicts appear automatically

- **SessionStart** in the originating project (channel 2): full claim text is
  injected with an instruction to raise the conflict with the user immediately.
- **Per-turn injection** (channel 3): if a contested page is retrieved by the
  hybrid search hook, a `⚠ contested — existing: X · new: Y · unresolved.`
  line is inserted inline.
- **Ingest** (channel 1): if a user is present at detection, resolve inline
  instead of enqueuing.

## Related

- `scripts/conflicts.py` — resolution primitives
- `scripts/ingest.py::_detect_contradiction` — the LLM comparison call
- `.claude/hooks/session_start.py` — channel 2
- `.claude/hooks/prompt_inject.py` — channel 3
