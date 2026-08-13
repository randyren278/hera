"""ui.py — TTY-aware pretty-print layer for the installer.

Pure stdlib, no pip deps, importable standalone under stock ``python3`` from
the ``scripts/install`` directory. ``install.py`` imports this in-process and
``preflight.py`` imports it in a venv subprocess, so it must locate itself and
decide its style with no help from either caller.

The style decision is made once (at import) from the output stream and the
environment, following the pinned degradation precedence:

    NO_COLOR set        -> plain
    else FORCE_COLOR set -> styled
    else stdout not a tty -> plain
    else Windows (nt) without VT enabled -> plain
    else TERM=dumb       -> plain
    else                 -> styled

Any exception raised while detecting the terminal degrades to plain — detection
never crashes the installer.

Two rules define "plain":
  * ZERO ESC bytes (no ANSI SGR).
  * ZERO non-ASCII bytes — every glyph degrades to its 7-bit fallback, and the
    two sanctioned wording transliterations (``->`` for ``->`` arrows and ``--``
    for em-dashes) are applied to caller-supplied wording.

When styled, helpers emit the Unicode glyphs plus ANSI color and leave
caller wording (arrows / em-dashes) untouched.
"""
from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------
# glyph / token map (styled -> fallback), the ONLY transliterations allowed
# --------------------------------------------------------------------------
_SECTION = ("▸", ">")        # section marker  ▸ / >
_SUBLINE = ("•", "-")        # sub-line marker • / -
_SKIP = ("↷", "[skip]")      # skip status     ↷ / [skip]
_WARN = ("⚠", "[warn]")      # warn status     ⚠ / [warn]
_OK = ("✔", "[ok]")          # ok status       ✔ / [ok]
_FAIL = ("✘", "[FAIL]")      # fail status     ✘ / [FAIL]

# box corners / rules: styled -> fallback
_BOX_TL = ("╭", "+")         # ╭ / +
_BOX_TR = ("╮", "+")         # ╮ / +
_BOX_BL = ("╰", "+")         # ╰ / +
_BOX_BR = ("╯", "+")         # ╯ / +
_BOX_H = ("─", "-")          # ─ / -
_BOX_V = ("│", "|")          # │ / |

# wording transliterations applied on the fallback path only
_ARROW = "→"                 # → -> "->"
_EMDASH = "—"                # — -> "--"

# ANSI SGR (styled path only)
_RESET = "\x1b[0m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"


def _detect_styled() -> bool:
    """Decide once whether to emit styled (glyph + color) output.

    Follows the pinned precedence exactly. Any exception -> plain.
    """
    try:
        env = os.environ
        if "NO_COLOR" in env:
            return False
        if "FORCE_COLOR" in env:
            return True
        stream = sys.stdout
        isatty = getattr(stream, "isatty", None)
        if not (callable(isatty) and isatty()):
            return False
        if os.name == "nt" and not _windows_vt_enabled():
            return False
        if env.get("TERM") == "dumb":
            return False
        return True
    except Exception:
        return False


def _windows_vt_enabled() -> bool:
    """True iff the Windows console has virtual-terminal processing enabled.

    Best-effort; any failure -> False (caller degrades to plain).
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VT = 0x0004
        return bool(mode.value & ENABLE_VT)
    except Exception:
        return False


_STYLED = _detect_styled()


def styled() -> bool:
    """Whether this process is emitting styled output (glyphs + color)."""
    return _STYLED


def _g(pair: tuple[str, str]) -> str:
    """Pick the styled or fallback member of a glyph pair."""
    return pair[0] if _STYLED else pair[1]


def _wording(msg: str) -> str:
    """Caller wording as it should appear on the current path.

    Styled path: unchanged. Fallback path: the two sanctioned transliterations
    (arrow, em-dash) are applied so the piped output stays 7-bit ASCII.
    """
    if _STYLED:
        return msg
    return msg.replace(_ARROW, "->").replace(_EMDASH, "--")


def _color(text: str, sgr: str) -> str:
    """Wrap ``text`` in an SGR color on the styled path; identity on fallback."""
    if not _STYLED:
        return text
    return f"{sgr}{text}{_RESET}"


# --------------------------------------------------------------------------
# public surface
# --------------------------------------------------------------------------

def header(title: str, rows: "dict[str, str]") -> None:
    """Boxed banner with a content-driven width (option B).

    Width grows to fit the longest of the title or any ``"{k}  {v}"`` row; it
    never truncates and never caps to the terminal width. Long paths are safe.
    """
    row_texts = [f"{k}  {v}" for k, v in rows.items()]
    content_lines = [title, *row_texts]
    inner = max((len(_wording(s)) for s in content_lines), default=0)
    pad = 1  # one space of padding on each side inside the box
    width = inner + pad * 2

    tl, tr = _g(_BOX_TL), _g(_BOX_TR)
    bl, br = _g(_BOX_BL), _g(_BOX_BR)
    h, v = _g(_BOX_H), _g(_BOX_V)

    top = tl + h * width + tr
    bottom = bl + h * width + br
    print(_color(top, _CYAN))
    _box_line(title, inner, pad, v, bold=True)
    for rt in row_texts:
        _box_line(rt, inner, pad, v, bold=False)
    print(_color(bottom, _CYAN))


def _box_line(text: str, inner: int, pad: int, v: str, bold: bool) -> None:
    body = _wording(text)
    filled = body + " " * (inner - len(body))
    inner_text = " " * pad + filled + " " * pad
    if _STYLED and bold:
        inner_text = f"{_BOLD}{inner_text}{_RESET}"
    left = _color(v, _CYAN)
    right = _color(v, _CYAN)
    print(f"{left}{inner_text}{right}")


def step(title: str) -> None:
    """Section line owning the ``▸`` / ``>`` marker, cyan."""
    marker = _color(_g(_SECTION), _CYAN)
    print(f"{marker} {_wording(title)}")


def info(msg: str) -> None:
    """Indented sub-line owning the ``•`` / ``-`` marker, dim."""
    marker = _color(_g(_SUBLINE), _DIM)
    print(f"  {marker} {_wording(msg)}")


def skip(msg: str) -> None:
    """Skip status: dim ``↷ <msg>`` (styled) / ``[skip] <msg>`` (fallback)."""
    if _STYLED:
        print(f"  {_color(_g(_SKIP) + ' ' + msg, _DIM)}")
    else:
        print(f"  {_g(_SKIP)} {_wording(msg)}")


def warn(msg: str) -> None:
    """Warn status: yellow ``⚠ <msg>`` (styled) / ``[warn] <msg>`` (fallback)."""
    if _STYLED:
        print(f"  {_color(_g(_WARN), _YELLOW)} {_wording(msg)}")
    else:
        print(f"  {_g(_WARN)} {_wording(msg)}")


def ok(name: str) -> None:
    """Pass status: ``✔ <name>`` (styled) / ``[ok]   <name>`` (fallback)."""
    if _STYLED:
        print(f"  {_color(_g(_OK), _GREEN)} {_wording(name)}")
    else:
        print(f"  {_g(_OK)}   {_wording(name)}")


def fail(name: str) -> None:
    """Fail status: ``✘ <name>`` (styled) / ``[FAIL] <name>`` (fallback)."""
    if _STYLED:
        print(f"  {_color(_g(_FAIL), _RED)} {_wording(name)}")
    else:
        print(f"  {_g(_FAIL)} {_wording(name)}")


def plain(msg: str) -> None:
    """Passthrough line — no marker, but still degrade wording on fallback."""
    print(_wording(msg))
