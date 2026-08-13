"""ingest.py — Single Source Ingest engine (design §9.1).

Invoked by:
  - .claude/skills/brain-ingest/SKILL.md (interactive slash command)
  - .claude/hooks/session_end_file.py (background filing, CP-6)
  - scripts/e2e_ingest.sh (CP-2 checkpoint)

Contract:
  ingest_source(source_path, source_kind="file", conn=None) -> Result
  Result is a dict: sources[], concepts[], entities[], log_entry, warnings[]

Flow:
  1. Read the source (already fetched/cleaned by the caller).
  2. Call `claude -p` with a strict JSON schema to extract:
     - one source summary (title, one_line, key_takeaways, citations)
     - N concept pages (title, one_line, body, wikilinks)
     - N entity pages (title, one_line, kind, body, wikilinks)
  3. Assign ULIDs to every new page.
  4. Write each page under wiki/{sources,concepts,entities}/ through the
     locking protocol (§6). Existing pages are updated in place; new ones
     are created with the full frontmatter schema (§4.2).
  5. Upsert `pages` rows, refresh `pages_fts` + `pages_vec` rows.
  6. Update `wiki/hot.md` (overwrite, ≤500 words), append to `wiki/index.md`,
     prepend to `wiki/log.md`.
  7. Contradiction detection is STUBBED at CP-2 — the LLM extractor is
     instructed to *report* possible contradictions in `warnings`, but no
     `conflicts` row is created. Full pipeline lands in CP-5.

Determinism note: pages are Claude-authored so exact word counts vary,
but the checkpoint asserts on properties (source page exists, has ULID,
DB rows consistent, hot/index/log updated), not exact strings.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import ulid

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import brain_db  # noqa: E402
import embed as _embed  # noqa: E402
import locks  # noqa: E402


WIKI = REPO / "wiki"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
CLAUDE_MODEL = os.environ.get("BRAIN_CLAUDE_MODEL", "sonnet")

# Isolation flags for nested `claude -p` calls. These REPLACE --bare.
#
# --bare skipped hooks, but it also skips keychain reads, and its auth is
# strictly ANTHROPIC_API_KEY or apiKeyHelper -- OAuth is never read. On a
# subscription that means every nested call dies with "Not logged in".
#
# --setting-sources "" loads no user/project/local settings, so no hook from
# any source can fire -- the same hook surface --bare covered. The real gain
# over --bare is --strict-mcp-config and --tools "", which additionally
# isolate MCP servers and tools, something --bare never did.
CLAUDE_ISOLATION = [
    "--setting-sources", "",      # no settings files -> no hooks
    "--strict-mcp-config",        # no MCP servers
    "--tools", "",                # no filesystem access
    "--disable-slash-commands",
]

# Nested calls run here so CLAUDE.md auto-discovery (which --bare used to
# suppress) finds nothing to inject into the extraction prompt.
CLAUDE_CWD = tempfile.gettempdir()


EXPLICIT_STATEMENT_PROMPT = """\
You are checking whether a user made an EXPLICIT statement of a specific fact
inside a chat transcript. Explicit means: the user said the fact directly, in
their own voice, as their view — not merely as a question, hypothetical, or
implication. Reading text authored by the ASSISTANT does not count.

Return ONE JSON object:

{{"explicit": true | false, "quote": "verbatim quote from a user turn if explicit, else \\"\\""}}

CLAIM: {claim}

TRANSCRIPT:
---
{transcript}
---

Return ONLY the JSON.
"""


def _user_stated_explicitly(transcript_text: str, claim: str) -> tuple[bool, str]:
    """ADR-11 primitive: did the user explicitly state `claim` in `transcript_text`?"""
    prompt = EXPLICIT_STATEMENT_PROMPT.format(
        claim=claim[:1000],
        transcript=transcript_text[:20000],
    )
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=300, cwd=CLAUDE_CWD)   # _user_stated_explicitly
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, ""
    if r.returncode != 0:
        return False, ""
    out = r.stdout.strip()
    if out.startswith("```"):
        out = re.sub(r"^```(?:json)?\s*", "", out)
        out = re.sub(r"\s*```$", "", out)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return False, ""
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return False, ""
    return bool(data.get("explicit")), str(data.get("quote", ""))


CONTRADICTION_PROMPT = """\
You are a fact-consistency checker for a personal knowledge vault. You receive
one page's CURRENT content and a NEW claim about the same topic. Decide
whether the new claim CONTRADICTS a specific fact stated in the current page.

Return ONE JSON object matching this exact schema — nothing else, no prose:

{{
  "verdict": "contradiction" | "no_contradiction" | "elaboration",
  "claim_old": "one-sentence quote or paraphrase of the specific claim in the current page that is contradicted (empty if verdict != contradiction)",
  "claim_new": "one-sentence summary of the specific new claim that contradicts (empty if verdict != contradiction)",
  "reason": "one sentence of why"
}}

Rules:
- verdict=contradiction ONLY when a specific factual claim in each disagrees.
  "Different level of detail" = elaboration, not contradiction.
  "The new page mentions something the old page did not" = no_contradiction.
- Be conservative: false positives flood the conflict queue and annoy the user.

CURRENT PAGE:
---
{old}
---

NEW CONTENT:
---
{new}
---

Return ONLY the JSON.
"""


def _detect_contradiction(old_body: str, new_body: str) -> dict | None:
    """Call Claude to compare old vs new. Returns a dict with verdict/claim_old/claim_new
    or None on error. Called once per page that already exists in the vault."""
    prompt = CONTRADICTION_PROMPT.format(old=old_body[:8000], new=new_body[:8000])
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=300, cwd=CLAUDE_CWD)   # _detect_contradiction
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    if out.startswith("```"):
        out = re.sub(r"^```(?:json)?\s*", "", out)
        out = re.sub(r"\s*```$", "", out)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _existing_page_at(conn, path: pathlib.Path) -> tuple[str, str] | None:
    """Look up (page_id, body_md) for a page currently at `path`, if any.
    Body is read from disk (source of truth); page_id comes from `pages`.
    Match by relative path (relative to REPO)."""
    path = pathlib.Path(path).resolve()
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        # Path outside REPO — cannot correspond to a vault page.
        return None
    row = conn.execute(
        "SELECT id FROM pages WHERE path = ? AND archived_at IS NULL",
        (rel,),
    ).fetchone()
    if not row:
        return None
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8", errors="replace")
    # Strip frontmatter for the comparison.
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    return row[0], body


def _enqueue_conflict(conn, page_id: str, source_new_id: str,
                      claim_old: str, claim_new: str, origin_cwd: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO conflicts(page_id, claim_old, claim_new, source_new_id, origin_cwd, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (page_id, claim_old, claim_new, source_new_id, origin_cwd, now),
    )
    conn.commit()


EXTRACTION_PROMPT = """\
You are the extraction engine for a personal knowledge vault. You will be
given the RAW TEXT of a single source (an article, a note, a transcript).
Return ONE JSON object matching this exact schema — nothing else, no prose,
no code fence, no preamble.

{{
  "source": {{
    "title": "short title for the source",
    "one_line": "one sentence: what this source is",
    "key_takeaways": ["bullet 1", "bullet 2", "..."],
    "body": "1-3 paragraph summary of the source"
  }},
  "concepts": [
    {{
      "title": "Concept Name",
      "one_line": "one sentence",
      "body": "explanatory markdown (1-4 short paragraphs). Use [[Wikilinks]] to entities/other concepts.",
      "aliases": []
    }}
  ],
  "entities": [
    {{
      "title": "Entity Name",
      "kind": "person | org | product | repo",
      "one_line": "one sentence",
      "body": "1-2 short paragraphs. Use [[Wikilinks]] to concepts and other entities.",
      "aliases": []
    }}
  ],
  "warnings": [
    "free-form strings for anything the ingest engine should know (e.g. potential contradiction with 'Some Existing Page', ambiguous claim, etc.)"
  ]
}}

Rules:
- Between 1 and 8 concepts; between 0 and 8 entities. Only include a page if
  the source contains enough material to justify it.
- Concept and entity titles MUST be human-readable, TitleCase, no filename
  extensions, no path prefixes. They will become both filenames and wikilink
  targets.
- The `body` fields are markdown; do not include YAML frontmatter — the
  ingest engine will add it.
- Do not invent facts. If a claim needs sourcing beyond this document, say so
  in the `body` explicitly (e.g. "This claim is unsourced in the article.").
- Output ONLY the JSON object.

RAW SOURCE FOLLOWS:
---
{raw}
---
"""


@dataclass
class PageWrite:
    id: str
    title: str
    type: str  # source|concept|entity
    path: pathlib.Path
    body_md: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Result:
    source: PageWrite
    concepts: list[PageWrite] = field(default_factory=list)
    entities: list[PageWrite] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_entry: str = ""


# ---------- helpers ----------

def _slugify(title: str) -> str:
    """Convert a human title to a filename-safe slug. Preserves case per the
    Obsidian convention `Agentic Orchestration.md`."""
    s = title.strip()
    s = re.sub(r"[\/\\:*?\"<>|]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s[:120]  # generous cap; Obsidian handles long titles fine


def _frontmatter(page_id: str, title: str, type_: str, extra: dict | None = None,
                 aliases: list[str] | None = None) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "---",
        f"id: {page_id}",
        f"type: {type_}",
        f'title: "{title}"',
        "aliases: " + json.dumps(aliases or []),
        f"created: {now}",
        f"updated: {now}",
        "tags: []",
    ]
    for k, v in (extra or {}).items():
        if isinstance(v, str):
            # A handful of "identifier-shaped" keys are safe (and expected by
            # tooling) to emit unquoted: visibility, source_kind, etc.
            if k in ("visibility", "source_kind", "owner") and re.match(r"^[a-z][\w-]*$", v):
                lines.append(f"{k}: {v}")
            else:
                lines.append(f'{k}: "{v}"')
        else:
            lines.append(f"{k}: {json.dumps(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _read_text_or_die(p: pathlib.Path) -> str:
    if not p.exists():
        raise SystemExit(f"ingest: source not found: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


def _call_claude_extract(raw: str) -> dict:
    """Call `claude -p` with the extraction prompt and return the parsed JSON.

    Uses subprocess with the prompt on stdin so we avoid arg-length issues on
    large sources. On any failure — network, non-zero exit, JSON parse — the
    caller sees an exception; the ingest run halts (the checkpoint will fail,
    and the executor retries per protocol).

    CLAUDE_ISOLATION skips every settings source, so no hook -- ours or
    anyone else's -- can fire, and the call cannot reach MCP servers or the
    filesystem. We deliberately do NOT use --bare: it skips keychain reads,
    so a subscription login is invisible and the call fails "Not logged in".
    cwd=CLAUDE_CWD keeps this vault's CLAUDE.md out of the prompt.
    """
    prompt = EXTRACTION_PROMPT.format(raw=raw[:200_000])  # generous cap
    cmd = [CLAUDE_BIN, "-p", *CLAUDE_ISOLATION,
           "--output-format", "text", "--model", CLAUDE_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=600, cwd=CLAUDE_CWD)   # _call_claude_extract
    except FileNotFoundError as e:
        raise RuntimeError(
            f"claude binary not found ({CLAUDE_BIN!r}); set CLAUDE_BIN or put it on PATH"
        ) from e
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed: exit {r.returncode}\nstderr:\n{r.stderr}")
    out = r.stdout.strip()
    # Be forgiving: strip a stray code fence if the model added one.
    if out.startswith("```"):
        out = re.sub(r"^```(?:json)?\s*", "", out)
        out = re.sub(r"\s*```$", "", out)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        # Attempt to recover the first {...} block.
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            raise RuntimeError(f"claude did not return JSON:\n{out[:2000]}") from e
        data = json.loads(m.group(0))
    return data


# ---------- DB writes ----------

def _upsert_page(conn, pw: PageWrite):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO pages(id, title, aliases, type, path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  title=excluded.title, aliases=excluded.aliases, path=excluded.path, updated_at=excluded.updated_at",
        (pw.id, pw.title, json.dumps(pw.aliases), pw.type,
         pw.path.relative_to(REPO).as_posix(), now, now),
    )


def _index_page_search(conn, pw: PageWrite):
    """Rebuild pages_fts + pages_vec rows for this page."""
    # Ensure the FTS map table exists (search.py creates it lazily too).
    conn.execute("""CREATE TABLE IF NOT EXISTS pages_fts_map (
        rowid   INTEGER PRIMARY KEY,
        page_id TEXT NOT NULL UNIQUE REFERENCES pages(id)
    )""")

    row = conn.execute("SELECT rowid FROM pages_fts_map WHERE page_id = ?",
                       (pw.id,)).fetchone()
    if row:
        rowid = row[0]
        conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (rowid,))
        conn.execute("INSERT INTO pages_fts(rowid, title, body) VALUES (?, ?, ?)",
                     (rowid, pw.title, pw.body_md))
    else:
        cur = conn.execute("INSERT INTO pages_fts(title, body) VALUES (?, ?)",
                           (pw.title, pw.body_md))
        rowid = cur.lastrowid
        conn.execute("INSERT INTO pages_fts_map(rowid, page_id) VALUES (?, ?)",
                     (rowid, pw.id))

    # Embedding: title + first ~500 chars of body — enough signal for retrieval
    # without embedding an entire book of text.
    payload = f"{pw.title}\n{pw.body_md[:2000]}"
    vec = _embed.embed(payload)
    conn.execute("DELETE FROM pages_vec WHERE page_id = ?", (pw.id,))
    conn.execute("INSERT INTO pages_vec(page_id, embedding) VALUES (?, ?)",
                 (pw.id, _embed.pack(vec)))


def _write_page_file(conn, pw: PageWrite, extra_fm: dict | None = None):
    pw.path.parent.mkdir(parents=True, exist_ok=True)
    with locks.lock(pw.path, page_id=pw.id, conn=conn,
                    intent="append", delta_body="") as acq:
        if acq is None:
            # Under contention — writing a page for the first time makes no
            # sense to delta-append. In practice ingest runs single-threaded
            # so this branch is defensive only.
            return
        fm = _frontmatter(pw.id, pw.title, pw.type, extra=extra_fm, aliases=pw.aliases)
        pw.path.write_text(fm + pw.body_md.rstrip() + "\n", encoding="utf-8")


# ---------- hot / index / log ----------

def _update_hot(conn, result: Result) -> None:
    hot = WIKI / "hot.md"
    hot.parent.mkdir(parents=True, exist_ok=True)
    fm = _frontmatter(str(ulid.new()) if not hot.exists() else _read_hot_id(hot),
                      "Hot Cache", "meta")
    body_lines = [
        "# Recent Context",
        "",
        "## Last Updated",
        f"{time.strftime('%Y-%m-%d')}. Ingested [[{result.source.title}]].",
        "",
        "## Key Recent Facts",
    ]
    for line in _first_takeaways(result.source.body_md, n=3):
        body_lines.append(f"- {line}")
    created_titles = ", ".join(f"[[{p.title}]]" for p in result.concepts + result.entities)
    body_lines += ["", "## Recent Changes",
                   f"- Created: {created_titles or '(none)'}",
                   "", "## Active Threads",
                   f"- Currently ingesting: [[{result.source.title}]]"]
    with locks.lock(hot, page_id=str(ulid.new()), conn=conn, allow_delta=False):
        hot.write_text(fm + "\n".join(body_lines) + "\n", encoding="utf-8")


def _read_hot_id(p: pathlib.Path) -> str:
    text = p.read_text()
    m = re.search(r"^id:\s*(\S+)", text, re.M)
    return m.group(1) if m else str(ulid.new())


def _first_takeaways(body: str, n: int = 3) -> list[str]:
    lines = [l.strip("-* ").strip() for l in body.splitlines() if l.strip()]
    return lines[:n]


def _update_index(conn, result: Result) -> None:
    idx = WIKI / "index.md"
    idx.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if not idx.exists():
        header = (_frontmatter(str(ulid.new()), "Index", "meta")
                  + "# Index\n\nEvery page, one line each.\n\n")
        idx.write_text(header)
    new_lines = []
    for p in [result.source] + result.concepts + result.entities:
        new_lines.append(f"- [[{p.title}]] — {_one_line(p.body_md)}")
    with locks.lock(idx, page_id=str(ulid.new()), conn=conn, allow_delta=False):
        with idx.open("a", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")


def _one_line(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:180]
    return ""


def _update_log(conn, result: Result, raw_path: pathlib.Path | None = None) -> None:
    log = WIKI / "log.md"
    src_ref = raw_path.relative_to(REPO).as_posix() if raw_path else result.source.path.relative_to(REPO).as_posix()
    entry_lines = [
        f"## [{time.strftime('%Y-%m-%d')}] ingest | {result.source.title}",
        f"- Source: `{src_ref}`",
        f"- Summary: [[{result.source.title}]]",
        f"- Pages created: " + (", ".join(f"[[{p.title}]]" for p in result.concepts + result.entities) or "(none)"),
        f"- Pages updated: (none)",
        f"- Key insight: {_one_line(result.source.body_md)}",
        "",
    ]
    entry = "\n".join(entry_lines)
    result.log_entry = entry
    if not log.exists():
        head = _frontmatter(str(ulid.new()), "Log", "meta") + "# Log\n\n"
        log.write_text(head, encoding="utf-8")
    with locks.lock(log, page_id=str(ulid.new()), conn=conn, allow_delta=False):
        old = log.read_text(encoding="utf-8")
        # Split off frontmatter and header, keep them at top; entry goes right after.
        head_end = old.find("\n# Log\n")
        if head_end == -1:
            log.write_text(old + entry, encoding="utf-8")
        else:
            split = head_end + len("\n# Log\n\n")
            log.write_text(old[:split] + entry + old[split:], encoding="utf-8")


# ---------- main entry point ----------

def ingest_source(source_path: str, source_kind: str = "file",
                  raw_dir: pathlib.Path | None = None,
                  conn=None) -> Result:
    """Ingest a single source. Returns a Result. Writes to disk + DB.

    source_path — path to the source file (already fetched/cleaned).
    source_kind — file | url | image | session
    raw_dir     — where to preserve the raw source; default wiki/.raw/articles/
    """
    src = pathlib.Path(source_path).resolve()
    raw = _read_text_or_die(src)
    if conn is None:
        conn = brain_db.ensure_ready()

    # Preserve raw text
    raw_dir = raw_dir or (WIKI / ".raw" / "articles")
    raw_dir.mkdir(parents=True, exist_ok=True)
    kept = raw_dir / src.name
    if src.resolve() != kept.resolve():
        shutil.copy2(src, kept)

    # LLM extraction
    data = _call_claude_extract(raw)

    src_page = PageWrite(
        id=str(ulid.new()),
        title=data["source"]["title"],
        type="source",
        path=WIKI / "sources" / f"{_slugify(data['source']['title'])}.md",
        body_md="\n\n".join([
            f"> [!source] {data['source']['one_line']}",
            "## Key takeaways",
            "\n".join(f"- {k}" for k in data["source"].get("key_takeaways", []) or []),
            "## Summary",
            data["source"].get("body", "") or "",
        ]),
    )

    concepts = [PageWrite(
        id=str(ulid.new()),
        title=c["title"],
        type="concept",
        path=WIKI / "concepts" / f"{_slugify(c['title'])}.md",
        body_md=(f"> [!info] {c.get('one_line','')}\n\n" + c.get("body", "")),
        aliases=c.get("aliases", []) or [],
    ) for c in data.get("concepts", []) or []]

    entities = [PageWrite(
        id=str(ulid.new()),
        title=e["title"],
        type="entity",
        path=WIKI / "entities" / f"{_slugify(e['title'])}.md",
        body_md=(f"> [!info] ({e.get('kind','')}) {e.get('one_line','')}\n\n" + e.get("body", "")),
        aliases=e.get("aliases", []) or [],
    ) for e in data.get("entities", []) or []]

    # Write source page with source-specific frontmatter (§4.2)
    src_extra = {
        "source_url": source_path if source_kind == "url" else src.as_posix(),
        "source_kind": source_kind,
        "ingested": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "raw_path": kept.relative_to(REPO).as_posix(),
    }
    _write_page_file(conn, src_page, extra_fm=src_extra)
    # The source page must exist in `pages` BEFORE the contradiction loop:
    # _enqueue_conflict records source_new_id = src_page.id, and
    # conflicts.source_new_id is a FK -> pages(id). Upserting it here (rather
    # than only in the deferred batch below) keeps that FK satisfiable and
    # avoids the crash on any contradiction. src_page is never frozen, so it is
    # dropped from the deferred batch to stay indexed exactly once.
    _upsert_page(conn, src_page)
    _index_page_search(conn, src_page)

    # For each concept/entity: if a page already exists at the target path,
    # run a contradiction check against its current body. On verdict=contradiction,
    # freeze the target (do not write, do not upsert), enqueue a conflict row.
    origin_cwd = os.environ.get("BRAIN_ORIGIN_CWD") or os.getcwd()
    # Normalize so the SessionStart conflict-scope query (which normalizes the
    # session cwd identically) matches on every OS — Windows case/separator drift
    # would otherwise make the `origin_cwd = ?` equality silently miss.
    origin_cwd = os.path.normcase(os.path.normpath(origin_cwd))
    frozen: set[str] = set()  # page_ids we chose to freeze
    result_warnings: list[str] = list(data.get("warnings", []) or [])

    for pw in concepts + entities:
        existing = _existing_page_at(conn, pw.path)
        if existing:
            old_id, old_body = existing
            # Reuse the existing page's ULID so a non-conflicting re-ingest
            # UPDATES the row in place (ON CONFLICT(id)) instead of minting a
            # fresh ULID and inserting a second row at the same path (path has
            # no unique constraint, which would duplicate the FTS/vec index).
            pw.id = old_id
            v = _detect_contradiction(old_body, pw.body_md)
            if v and v.get("verdict") == "contradiction":
                # ADR-11: on source_kind='session', if the user explicitly
                # stated the new claim in the transcript, auto-resolve as
                # session-wins. Otherwise (all other source_kinds, or an
                # only-implied contradiction from a session) enqueue for review.
                # Pinned exception: if the EXISTING page is pinned (seed packs
                # pin their pages, but anything may be pinned), the user's new
                # content always wins — take the same ADR-11 resolve_new path
                # (no open conflict, page updated in place, not left
                # frozen/stale) rather than enqueuing.
                auto_resolved = False
                pinned_wins = bool(conn.execute(
                    "SELECT pinned FROM pages WHERE id=?", (old_id,)
                ).fetchone()[0])
                session_explicit = False
                if source_kind == "session":
                    session_explicit, _quote = _user_stated_explicitly(
                        raw, v.get("claim_new", ""))
                if session_explicit or pinned_wins:
                    # Enqueue then resolve_new via the conflicts primitive so
                    # the page gets the ## Superseded archive treatment consistently.
                    import conflicts as _conflicts  # local import to avoid cycles at load time
                    _enqueue_conflict(
                        conn, page_id=old_id, source_new_id=src_page.id,
                        claim_old=v.get("claim_old", "").strip(),
                        claim_new=v.get("claim_new", "").strip(),
                        origin_cwd=origin_cwd,
                    )
                    cid = conn.execute(
                        "SELECT id FROM conflicts WHERE page_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                        (old_id,),
                    ).fetchone()[0]
                    _conflicts.resolve_new(conn, cid)
                    reason = ("pinned override" if pinned_wins
                              else "ADR-11 explicit statement")
                    result_warnings.append(
                        f"auto-resolved ({reason}, user-wins) on [[{pw.title}]]"
                    )
                    auto_resolved = True
                    # resolve_new updated the existing page (old_id) in
                    # place. Freeze old_id so the fresh-ULID pw is NOT
                    # upserted below — otherwise a second pages row is
                    # minted for the same path (path has no unique
                    # constraint), duplicating the FTS/vec index too.
                    frozen.add(old_id)

                if not auto_resolved:
                    _enqueue_conflict(
                        conn,
                        page_id=old_id,
                        source_new_id=src_page.id,
                        claim_old=v.get("claim_old", "").strip(),
                        claim_new=v.get("claim_new", "").strip(),
                        origin_cwd=origin_cwd,
                    )
                    frozen.add(old_id)
                    result_warnings.append(
                        f"conflict enqueued on [[{pw.title}]]: {v.get('reason','no reason given')}"
                    )
                continue
        _write_page_file(conn, pw)

    # DB writes for pages that were NOT frozen. src_page was already upserted
    # and indexed above the loop (FK precondition), so it is excluded here.
    all_pages_to_index = [p for p in concepts + entities
                          if _page_id_should_index(conn, p, frozen)]
    for p in all_pages_to_index:
        _upsert_page(conn, p)
        _index_page_search(conn, p)
    conn.commit()

    # Meta pages
    result = Result(source=src_page, concepts=concepts, entities=entities,
                    warnings=result_warnings)
    _update_hot(conn, result)
    _update_index(conn, result)
    _update_log(conn, result, raw_path=kept)

    return result


def _page_id_should_index(conn, pw: PageWrite, frozen: set[str]) -> bool:
    """Return True if pw should be indexed. A frozen (contradicting) page is
    identified by its EXISTING id in `pages`; we look up by path so a new pw
    with a different id can still be frozen if the path already has a page."""
    existing = _existing_page_at(conn, pw.path)
    if existing and existing[0] in frozen:
        return False
    return True


# ---------- CLI ----------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="path to source file")
    ap.add_argument("--kind", default="file")
    ap.add_argument("--json", action="store_true", help="emit machine-readable summary")
    a = ap.parse_args()
    r = ingest_source(a.source, source_kind=a.kind)
    summary = {
        "source": {"id": r.source.id, "title": r.source.title,
                   "path": r.source.path.relative_to(REPO).as_posix()},
        "concepts": [{"id": p.id, "title": p.title,
                      "path": p.path.relative_to(REPO).as_posix()} for p in r.concepts],
        "entities": [{"id": p.id, "title": p.title,
                      "path": p.path.relative_to(REPO).as_posix()} for p in r.entities],
        "warnings": r.warnings,
    }
    if a.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"ingested: {r.source.title}")
        print(f"  concepts: {len(r.concepts)}")
        print(f"  entities: {len(r.entities)}")
        if r.warnings:
            print("  warnings:")
            for w in r.warnings: print(f"    - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
