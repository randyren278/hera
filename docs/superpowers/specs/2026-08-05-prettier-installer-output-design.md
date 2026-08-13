# Prettier installer output — design

**Date:** 2026-08-05
**Status:** approved design, pre-implementation
**Scope:** `install.py` + `scripts/install/preflight.py` console output only

---

## Goal

The installer currently prints flat, single-weight plain text. Give it visual
hierarchy and status color **on an interactive terminal**, while degrading to
clean 7-bit ASCII everywhere else (pipes, redirects, CI, legacy Windows
consoles). No behavior change — only how existing lines are rendered.

## Success criteria (falsifiable)

1. `python install.py --dry-run` **to a TTY** shows: a boxed header, `▸`
   section markers per step, a right-aligned `step N/7` counter, and
   `✔` (green) / `↷` (dim) status glyphs.
2. `python install.py --dry-run | cat` (not a TTY) emits **zero** `0x1b`
   (ESC) bytes and **zero** non-ASCII bytes. Verified by
   `python install.py --dry-run | LC_ALL=C grep -nP '[^\x00-\x7f]|\x1b'`
   returning no matches.
3. `NO_COLOR=1 python install.py --dry-run` on a TTY → no ESC bytes (color off,
   glyphs may remain per NO_COLOR spec — see Decisions).
4. Return codes, step order, subprocess calls, and control flow are byte-for-byte
   unchanged from `git show HEAD:install.py`. Verified by diffing a
   normalized (ANSI/glyph-stripped) run against the current output's wording.
5. No new pip dependency; module imports under the stock `python3` that runs
   `install.py` before the venv exists.

## Chosen visual style — "Option B, interactive form"

Approved via HTML mockups (`.superpowers/brainstorm/`), matched against the
**real** installer wording (verbatim — see below).

**Interactive (TTY):**
```
╭─ Second Brain · installer ──────────────────╮
│ vault  /Users/randy/second-brain            │
│ mode   install   home  ~/.claude            │
╰─────────────────────────────────────────────╯

▸ preflight                        step 1/7
    ✔ ensure .venv + reconcile pip deps
    ✔ ollama model present
▸ brain.db                         step 2/7
    ↷ brain.db already present — skipping
...
✔ install: complete.
```

**Fallback (piped / CI / legacy Windows):**
```
+-- Second Brain · installer ------------------+
| vault  /Users/randy/second-brain             |
+----------------------------------------------+

> preflight                          step 1/7
    [ok]   ensure .venv + reconcile pip deps
    [skip] brain.db already present — skipping
...
[ok] install: complete.
```

### Glyph / token mapping

| role    | TTY glyph | TTY color | fallback token |
|---------|-----------|-----------|----------------|
| success | `✔`       | green     | `[ok]  `       |
| info    | `▸` / `•` | blue/dim  | `>` / `-`      |
| skip    | `↷`       | dim       | `[skip]`       |
| warn    | `⚠`       | yellow    | `[warn]`       |
| fail    | `✘`       | red       | `[fail]`       |
| header box | `╭─╮│╰╯` | cyan     | `+-|`          |
| section marker | `▸` | cyan      | `>`            |

## Architecture

One new module: **`scripts/install/ui.py`** (pure stdlib). Exposes a shared
status vocabulary used by both files. Two design facts drive it:

- `install.py` imports `ui` **in-process**.
- `preflight.py` is run by `install.py` as a **subprocess under the venv
  python** — a separate process — so it imports `ui` too and decides color
  **independently** (its own `isatty()` / env check). The module must therefore
  carry no cross-process state; capability is computed at import/first-use in
  each process.

### `ui.py` public surface (proposed)

```python
supports_style() -> bool        # the degradation decision (see precedence)
header(title, rows: dict)       # boxed title + key/value rows
step(n, total, label)           # "▸ label                 step n/total"
ok(msg)  / info(msg) / skip(msg) / warn(msg) / fail(msg)   # indented status lines
done(msg)                       # final "✔ ..." success banner
```

Each status helper renders glyph+color when `supports_style()`, else the ASCII
token form. `install.py`'s existing `Runner.do()` routes through `info`/`ok`.

### Color/degradation precedence (highest wins)

1. `NO_COLOR` present (any value) → **plain** (no color). [no-color.org]
2. `FORCE_COLOR` present → **styled** (even if not a TTY).
3. `sys.stdout` is None / has no `isatty` / `isatty()` is False → **plain**.
4. `os.name == 'nt'` without enabled VT processing → **plain**.
5. `TERM == 'dumb'` → **plain**.
6. otherwise → **styled**.

Any exception anywhere in detection → **plain** (never crash to look pretty).

## Rewiring (verbatim-wording rule)

Per approved decision **"keep exact wording, restyle only"**: every step title
(`step 4/7: merge into settings.json`) and every sub-line (`merged.`,
`brain.db already present — skipping`, `removed N skill dir(s)`, etc.) stays
byte-for-byte identical. Only the marker/glyph/color/box **around** the text is
added. `preflight.py`'s check names and `ok`/`FAIL` semantics are unchanged;
its `_emit()` swaps to `ui.ok`/`ui.fail`, and its `preflight:` / `preflight: ok`
banners route through `ui.header`/`ui.done`.

### Two ratified deviations from strict verbatim (approved during build)

1. **Fallback transliteration of `—`/`→`.** Message text contains non-ASCII
   punctuation (`—`, `→`). "Byte-for-byte wording" and "zero non-ASCII when
   piped" cannot both hold for those lines. **Resolved (user):** in the plain
   fallback only, `ui._wording()` maps `—`→`--` and `→`→`->`; styled TTY output
   keeps them exact. These are the ONLY two sanctioned transliterations.
2. **Banner rewording (Option 3 header).** The two `install: vault=… / mode=…`
   banner lines are replaced by a titled box (`Second Brain - installer` with
   `vault` / `mode` / `home` rows) so the approved boxed header renders.
   **Resolved (user):** this is a deliberate, scoped wording change on the
   banner lines only; all other lines remain byte-for-byte. Title uses an ASCII
   `-` separator (not `·`) to stay pure-ASCII in the piped fallback. The
   uninstall banner mirrors it (`Second Brain - uninstaller`).

## Testing / verification

- **Degradation gate:** the grep in success-criterion #2, run for both
  `--dry-run` and a real `--uninstall --dry-run`.
- **Behavior-unchanged gate:** strip ANSI+glyphs from a styled run, compare the
  remaining wording to `git stash`ed current output — must match line-for-line.
- **Adversarial verify:** a subagent tries to prove the degradation matrix
  wrong (TTY, pipe, `NO_COLOR`, `FORCE_COLOR`, `os.name=='nt'` no-VT,
  `TERM=dumb`, closed stdout) by reading `ui.py`.
- **Live proof:** run `install.py --dry-run` to terminal (see color) and piped
  to `cat` (see clean ASCII) before finishing.

## Out of scope

- Rewording any step/sub-line text (explicitly rejected).
- Progress bars / spinners / animation.
- Touching any file other than `install.py` and `scripts/install/preflight.py`
  (plus the new `ui.py`).
- The README Install block (already fixed separately this session).

## Risks

- **Escape bytes leaking into a pipe** — the one true failure mode; guarded by
  criterion #2 and the adversarial pass.
- **Windows legacy console** rendering raw ANSI — mitigated by precedence rule
  #4 (nt-without-VT → plain).
- **Verbatim-wording drift** — mitigated by the behavior-unchanged gate.
