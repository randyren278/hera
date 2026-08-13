"""locks.py — per-file locking + delta overflow (design §6, ADR-02).

Contract: every vault write goes through `with lock(path): ...`. On lock
contention after N retries the caller writes a delta file to
wiki/.pending/<page_id>.<ulid>.delta.md instead — the write never blocks
indefinitely. On the next successful lock acquisition for the same page,
outstanding deltas are merged first.

Design notes:
  - lockfile format: single line "<pid> <iso_timestamp>", atomic O_EXCL create
  - retry: 3 attempts with exponential-ish backoff (~5s total)
  - stale lock: age > STALE_SECONDS OR the recorded PID is dead → broken
  - delta files carry `intent:` frontmatter: append | replace_section | frontmatter_patch
  - `pending_deltas` DB rows track outstanding files for --doctor and observability
"""
from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import sqlite3
import time
from dataclasses import dataclass

import ulid


DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.6  # seconds base; total ≈ 4.9s over 3 retries
STALE_SECONDS = 10 * 60  # 10 minutes


class LockError(Exception):
    """Raised when a caller genuinely wants to fail closed on contention.
    The normal flow does NOT raise — it writes a delta and returns None."""


@dataclass
class LockAcquired:
    path: pathlib.Path
    lock_path: pathlib.Path


def _lock_path_for(target: pathlib.Path) -> pathlib.Path:
    """`wiki/concepts/foo.md` → `wiki/concepts/.foo.md.lock`"""
    return target.parent / f".{target.name}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # NEVER call os.kill(pid, sig) on Windows — even signal 0 is routed to
        # TerminateProcess and would kill the process. Probe non-destructively
        # via OpenProcess; if we can't open it, fall back to `tasklist`. On any
        # uncertainty, treat the pid as alive so we never break a live lock.
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists and belongs to another user — treat as alive.
        return True


def _pid_alive_windows(pid: int) -> bool:
    # Try the Win32 API first (no signal, purely a handle open + query).
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Could not open — either gone (ERROR_INVALID_PARAMETER) or access
            # denied (exists, other user). Access-denied means alive.
            ERROR_ACCESS_DENIED = 5
            return kernel32.GetLastError() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    # Fallback: shell out to tasklist. On any failure, treat as alive (safe).
    try:
        import subprocess
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in r.stdout
    except Exception:
        return True


def _is_stale(lock_path: pathlib.Path, now: float) -> bool:
    try:
        st = lock_path.stat()
    except FileNotFoundError:
        return False
    if (now - st.st_mtime) > STALE_SECONDS:
        return True
    try:
        text = lock_path.read_text().strip().split()
        if text:
            pid = int(text[0])
            if not _pid_alive(pid):
                return True
    except (ValueError, OSError):
        return True
    return False


def _try_create(lock_path: pathlib.Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as e:
        if e.errno == errno.EEXIST:
            return False
        raise
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return True


def _break_stale(lock_path: pathlib.Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def lock(target: pathlib.Path,
         retries: int = DEFAULT_RETRIES,
         backoff: float = DEFAULT_BACKOFF,
         allow_delta: bool = True,
         page_id: str | None = None,
         conn: sqlite3.Connection | None = None,
         intent: str = "append",
         delta_body: str | None = None):
    """Acquire a per-file lock.

    Yields a LockAcquired on success. On contention with `allow_delta=True`,
    yields None and writes a delta file (caller must have provided
    page_id + delta_body). With `allow_delta=False`, raises LockError.
    """
    target = pathlib.Path(target)
    lp = _lock_path_for(target)
    now = time.time()

    for attempt in range(retries + 1):
        # Break stale first, then try to create.
        if _is_stale(lp, now):
            _break_stale(lp)
        if _try_create(lp):
            # Before yielding, merge any pending deltas for this page.
            if page_id and conn is not None:
                _merge_deltas(conn, page_id, target)
            try:
                yield LockAcquired(path=target, lock_path=lp)
            finally:
                try:
                    lp.unlink()
                except FileNotFoundError:
                    pass
            return
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt) * 0.5)  # ~0.8, 1.6, 3.2s → sum ≈ 5.6s
            now = time.time()

    # Contention persists.
    if not allow_delta:
        raise LockError(f"could not acquire lock: {lp}")

    if not page_id or delta_body is None or conn is None:
        # No delta info supplied — best we can do is raise; callers using
        # the delta path must pass page_id/delta_body/conn.
        raise LockError(
            f"lock contention on {lp} and delta path not configured "
            "(need page_id, delta_body, conn)"
        )

    _write_delta(conn, page_id, target, intent, delta_body)
    yield None  # signal "wrote a delta instead"


def _pending_dir(target: pathlib.Path) -> pathlib.Path:
    # Walk up to find wiki/ (or its equivalent); fall back to sibling `.pending`.
    for parent in target.parents:
        if parent.name == "wiki":
            return parent / ".pending"
    return target.parent / ".pending"


def _write_delta(conn: sqlite3.Connection, page_id: str, target: pathlib.Path,
                 intent: str, body: str) -> pathlib.Path:
    pending = _pending_dir(target)
    pending.mkdir(parents=True, exist_ok=True)
    uid = str(ulid.new())
    path = pending / f"{page_id}.{uid}.delta.md"
    header = (
        "---\n"
        f"page_id: {page_id}\n"
        f"target: {target.as_posix()}\n"
        f"intent: {intent}\n"
        f"created: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        "---\n"
    )
    path.write_text(header + body)
    with conn:
        conn.execute(
            "INSERT INTO pending_deltas(page_id, delta_path, created_at) VALUES (?, ?, ?)",
            (page_id, path.as_posix(), time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
    return path


def _merge_deltas(conn: sqlite3.Connection, page_id: str, target: pathlib.Path) -> int:
    """Merge outstanding deltas for `page_id` into `target`.

    Simple policy for v1: apply deltas in `created_at` order. `append` deltas
    are appended to the target body. `replace_section` and `frontmatter_patch`
    are recorded as merged but only the append path is fully implemented; the
    others are TODO (CP-2 will exercise them via the ingest flow if needed).
    """
    rows = conn.execute(
        "SELECT id, delta_path FROM pending_deltas "
        "WHERE page_id = ? AND merged_at IS NULL ORDER BY created_at",
        (page_id,),
    ).fetchall()
    if not rows:
        return 0

    for row_id, delta_path in rows:
        p = pathlib.Path(delta_path)
        if not p.exists():
            # Delta file vanished — mark merged so we don't spin on it.
            with conn:
                conn.execute(
                    "UPDATE pending_deltas SET merged_at = ? WHERE id = ?",
                    (time.strftime("%Y-%m-%dT%H:%M:%S"), row_id),
                )
            continue
        text = p.read_text()
        # Split frontmatter and body.
        body = text
        intent = "append"
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                fm = text[4:end]
                body = text[end + 5:]
                for ln in fm.splitlines():
                    if ln.startswith("intent:"):
                        intent = ln.split(":", 1)[1].strip()
        if intent == "append":
            with target.open("a", encoding="utf-8") as f:
                f.write("\n" + body.rstrip() + "\n")
        # (replace_section / frontmatter_patch: TODO — deliberately noisy.)
        else:
            with target.open("a", encoding="utf-8") as f:
                f.write(f"\n<!-- unmerged intent={intent} — see {p.name} -->\n")
        with conn:
            conn.execute(
                "UPDATE pending_deltas SET merged_at = ? WHERE id = ?",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), row_id),
            )
    return len(rows)


def sweep_stale_locks(root: pathlib.Path) -> int:
    """Utility for `--doctor` / cron: break stale locks under `root`."""
    now = time.time()
    broken = 0
    for lp in root.rglob(".*.lock"):
        if _is_stale(lp, now):
            _break_stale(lp)
            broken += 1
    return broken
