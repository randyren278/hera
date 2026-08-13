---
name: brain-team
description: All team-brain operations in one place. add (publish your redacted pages up to the shared repo), remove (un-publish your own pages — real delete, owner-scoped, human-gated), retrieve (search the team brain + your vault), pull (sync). Use when the user says "share/publish this to team", "remove/unpublish my team pages", "what does the team know about X", "what did <teammate> publish", or "search the team brain".
---

# brain-team

One skill for the whole team-brain loop. Your redacted knowledge goes **up**
(`add`), comes back **down** and is searched (`retrieve`), and you can pull
**your own** pages back out when a project ends (`remove`). This consolidates
what used to be two separate publish/retrieve skills into one honest verb
router (`add` *publishes*, so the old "ingest-private" name is gone).

> **Vault location.** Requires the vault root, written by `install.py` into
> `~/.claude/second-brain.env` as `SECOND_BRAIN_VAULT`. If unset, error and tell
> the user to run `python install.py` from the vault first.

**Invocation convention (OS-neutral).** Every engine call uses the one launcher
`scripts/brain_cli.py`, which self-locates the vault, resolves the venv
interpreter for the running OS, and re-execs the engine. Run it as:

    python "<VAULT>/scripts/brain_cli.py" <engine> [args...]

replacing `<VAULT>` with the absolute path from `SECOND_BRAIN_VAULT` (the one-line
locator `~/.claude/second-brain.env`). Works identically on Windows and POSIX.

## Verb router

| User says | Verb | Engine |
|---|---|---|
| "share/publish this to team", "add to team brain" | `add <path>` | `publish.py` |
| "remove / unpublish my team pages", "pull my stuff back" | `remove` | `team_remove.py` + `publish.py push` |
| "what does the team know about X", "search team brain" | `retrieve [--owner X] <q>` | `team_sync.py` + `team_search.py` |
| "what did <teammate> publish about Y" | `retrieve --owner <name> <q>` | same, owner-scoped |
| "sync the team brain" | `pull` | `team_sync.py` |

---

## add `<path>` — publish your redacted knowledge up

Publish your redacted knowledge up to the team brain. Manual-only; nothing
reaches the shared repo without the human diff review.

1. Private ingest: `python "<VAULT>/scripts/brain_cli.py" ingest <path>`
   (or delegate to `/brain-ingest`). Writes the private pages, enqueues conflicts.
2. Strip + stage: `python "<VAULT>/scripts/brain_cli.py" publish stage <path> --json`.
   Runs the LLM redactor on every produced page and writes public-safe copies
   into `team-brain-staging/<owner>/{sources,concepts,entities}/`, each stamped
   `visibility: public` and `owner: <you>`.
3. Show the diff: `python "<VAULT>/scripts/brain_cli.py" publish diff`.
   Present the full unified diff. **Wait for explicit confirmation.**
4. On confirmation only: `python "<VAULT>/scripts/brain_cli.py" publish push -m "..."`.
   On rejection: leave staging as-is; report what was staged and what was held back.

Strip rules (defense in depth; the human diff review is the safety mechanism):
removes personal info, named private individuals, subjective claims; keeps
facts, patterns, definitions, well-known public entities.

## remove — un-publish YOUR OWN pages (real delete)

For when a project ends and you want your context out of the shared space.
This is a **real deletion** (git rm), **owner-scoped** (only ever
`team-brain-staging/<you>/`), and **human-gated** (same as add). Git history
is the undo — there is no `.archive` mirror on the team side.

1. Fresh view: `python "<VAULT>/scripts/brain_cli.py" team_sync clone-or-pull`.
   **Check the result — a stale view here would list the wrong pages to delete.**
   Exit 0 (fresh pull, or the empty-remote / no-team no-ops) → proceed. A
   **non-zero** exit is a real pull failure: warn the user the list may be stale
   and let them decide whether to continue, rather than staging removals against
   an out-of-date clone.
2. List *your* published pages: `python "<VAULT>/scripts/brain_cli.py" team_remove list --json`.
   Each item is `{title, path, added_date, type}`, newest first.
3. **Group the list conversationally to match the user's ask.** There is no
   stored project tag — you do the slicing here: "what did I push last week"
   → filter by `added_date`; "my RAG pages" → filter by title/topic; "show me
   everything" → the whole list. Present it and let the user pick which pages
   (by title, topic, or date range) to remove.
4. Stage the deletions: `python "<VAULT>/scripts/brain_cli.py" team_remove stage-remove <path>...`.
   This `git rm`s the chosen pages in the staging clone. It is fail-closed: if
   any path is outside your `<owner>/` folder, it refuses and stages nothing.
5. Show the diff: `python "<VAULT>/scripts/brain_cli.py" team_remove diff`.
   Present it. **Wait for explicit "push" / "confirm".** Never push unasked.
6. On confirmation only: `python "<VAULT>/scripts/brain_cli.py" publish push -m "brain: remove <what>"`.
   (Removal and publish share the one gated push path.) On rejection: leave the
   staged deletions in place and report them, or `git -C team-brain-staging reset`
   to unstage if the user wants to back out entirely.

## retrieve `[--owner <name>] <query>` — search the team brain + your vault

1. Sync first (pull only): `python "<VAULT>/scripts/brain_cli.py" team_sync clone-or-pull`.
   **Check the result before searching — never analyse stale data silently.**
   Exit 0 (a fresh pull, or the legitimate no-ops `No team space configured…`
   and `(remote is empty — nothing to pull)`) → proceed. A **non-zero** exit is
   a real pull failure (offline, non-ff, auth): the local clone is stale, so
   **warn the user that results may be out of date and let them decide** whether
   to search the stale copy or stop — do not quietly fall through to step 2.
2. Search: `python "<VAULT>/scripts/brain_cli.py" team_search [--owner <name>] --json "<query>"`.
   Team pages are indexed in a separate `team.db` with the SAME hybrid
   retrieval substrate as your personal vault — FTS5 **BM25** + dense
   sqlite-vec embeddings fused by **RRF** (k=60), keyed by each page's
   publisher ULID — so team and local knowledge are sorted and ranked
   identically. Search-all folds in your personal `wiki/` (owner `me`) via the
   local `hybrid_search`, merged by fused score; `--owner <name>` scopes to one
   teammate and excludes personal (you asked for *their* view).
3. Present grouped by owner; attribute team facts to their owner in prose. When
   you use a page in your answer, cite it as `(Source: [[Title]])`.

Note: when you ask an ordinary question, `prompt_inject.py` now surfaces
relevant team pages **inline** in the same ranked pointer block as local pages,
each tagged `(team: <owner>)` — you don't have to invoke `retrieve` explicitly
to see what the team knows.

## pull — sync only

`python "<VAULT>/scripts/brain_cli.py" team_sync clone-or-pull`.
Clones the fixed remote if absent, else fast-forward-pulls. An empty remote is
not an error. Never pushes.

## Invariants (do not violate)

- **Never push without explicit human approval** — `add` and `remove` both stage,
  show a diff, and wait. Publishing and un-publishing are equally consequential.
- **`remove` is owner-scoped** — only ever `team-brain-staging/<you>/`. You cannot
  delete a teammate's pages; that's their call, same as they can't delete yours.
- **Git history is the undo** — a removed page is recoverable from the team repo's
  history. No `.archive` mirror on the team side.
- **No brain.db writes for team pages** — team content stays out of your personal
  index. Team pages are indexed in a separate `team.db` (same BM25+dense+RRF
  substrate, keyed by publisher ULID); retrieval fuses the two DBs at query time
  but never writes team pages, vectors, or citations into `brain.db`.

## Related

- `scripts/publish.py` — strip + stage + gated push (add)
- `scripts/team_remove.py` — owner-scoped list + staged git-rm (remove)
- `scripts/team_search.py` — hybrid (BM25+dense+RRF) retrieval over team.db + vault
- `scripts/team_index.py` — builds team.db from staged Markdown, keyed by ULID
- `scripts/team_sync.py` — pull-only clone-or-pull (reindexes team.db on sync)
