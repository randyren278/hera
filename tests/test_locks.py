"""Concurrency tests for locks.py — per-file locking + delta overflow."""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import hera_db  # noqa: E402
import locks  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated wiki + hera.db under tmp_path."""
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / ".pending").mkdir()
    db_path = tmp_path / "hera.db"
    monkeypatch.setattr(hera_db, "DB_PATH", db_path)
    conn = hera_db.ensure_ready(db_path)
    # Seed one page so foreign keys on pending_deltas.page_id are satisfied.
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO pages(id,title,type,path,created_at,updated_at) "
        "VALUES ('01PAGE','Foo','concept',?,?,?)",
        ((wiki / "concepts" / "foo.md").as_posix(), now, now),
    )
    conn.commit()
    target = wiki / "concepts" / "foo.md"
    target.write_text("# Foo\n")
    yield {"wiki": wiki, "db": conn, "target": target}
    conn.close()


def test_simple_lock_write(env):
    with locks.lock(env["target"], page_id="01PAGE", conn=env["db"]) as acq:
        assert acq is not None
        with env["target"].open("a") as f:
            f.write("hello\n")
    assert "hello" in env["target"].read_text()
    # Lockfile removed on exit.
    assert not (env["target"].parent / ".foo.md.lock").exists()


def test_contention_writes_delta(env, monkeypatch):
    """With retries=0 and a squatting lockfile, the lock() call must write a delta
    and yield None instead of blocking."""
    lp = env["target"].parent / ".foo.md.lock"
    # Simulate a live squatter: current PID is alive, so lock is not stale.
    lp.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    delta_body = "extra content added while locked"
    with locks.lock(env["target"], retries=0, page_id="01PAGE",
                    conn=env["db"], intent="append", delta_body=delta_body) as acq:
        assert acq is None  # signals "wrote a delta"
    # One row in pending_deltas, one file in wiki/.pending/
    row = env["db"].execute(
        "SELECT page_id, delta_path, merged_at FROM pending_deltas"
    ).fetchone()
    assert row is not None
    assert row[0] == "01PAGE"
    assert pathlib.Path(row[1]).exists()
    assert row[2] is None  # not merged yet
    pending_files = list((env["wiki"] / ".pending").glob("01PAGE.*.delta.md"))
    assert len(pending_files) == 1
    assert delta_body in pending_files[0].read_text()
    # Clean up: remove squatter so subsequent acquisitions don't hang.
    lp.unlink()


def test_next_acquisition_merges_delta(env):
    """After a delta is queued, the next successful lock acquisition must
    merge it into the target file and stamp merged_at."""
    lp = env["target"].parent / ".foo.md.lock"
    lp.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    with locks.lock(env["target"], retries=0, page_id="01PAGE",
                    conn=env["db"], intent="append",
                    delta_body="delta-merged-content") as acq:
        assert acq is None
    lp.unlink()  # free the lock
    with locks.lock(env["target"], page_id="01PAGE", conn=env["db"]) as acq:
        assert acq is not None
    body = env["target"].read_text()
    assert "delta-merged-content" in body
    merged_at = env["db"].execute(
        "SELECT merged_at FROM pending_deltas"
    ).fetchone()[0]
    assert merged_at is not None


def test_stale_lock_broken_by_dead_pid(env):
    lp = env["target"].parent / ".foo.md.lock"
    # PID 999999 will (almost certainly) not exist on this machine.
    lp.write_text(f"999999 {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    with locks.lock(env["target"], retries=0, page_id="01PAGE", conn=env["db"]) as acq:
        assert acq is not None  # dead-PID lock was broken and re-acquired


def test_stale_lock_broken_by_age(env, monkeypatch):
    lp = env["target"].parent / ".foo.md.lock"
    lp.write_text(f"{os.getpid()} 2000-01-01T00:00:00\n")
    old = time.time() - 3600
    os.utime(lp, (old, old))
    with locks.lock(env["target"], retries=0, page_id="01PAGE", conn=env["db"]) as acq:
        assert acq is not None


def test_sweep_stale_locks(env):
    lp = env["target"].parent / ".foo.md.lock"
    lp.write_text(f"999999 x\n")
    old = time.time() - 3600
    os.utime(lp, (old, old))
    broken = locks.sweep_stale_locks(env["wiki"])
    assert broken == 1
    assert not lp.exists()


def test_delta_survives_a_full_rewrite_under_the_lock(env):
    """Regression: deltas were merged BEFORE yielding, so a caller that rewrites
    the whole file (ingest's _write_page_file) erased them while they were
    stamped merged."""
    lp = env["target"].parent / ".foo.md.lock"
    lp.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    with locks.lock(env["target"], retries=0, page_id="01PAGE", conn=env["db"],
                    intent="append", delta_body="queued-while-locked"):
        pass
    lp.unlink()
    with locks.lock(env["target"], page_id="01PAGE", conn=env["db"]):
        env["target"].write_text("# Foo rewritten\n")
    body = env["target"].read_text()
    assert body.startswith("# Foo rewritten") and "queued-while-locked" in body


def test_stale_break_never_removes_a_lock_that_was_just_retaken(env, monkeypatch):
    """TOCTOU: between judging a lock stale and breaking it, another writer can
    break it too and take a fresh one. The break must not delete that fresh lock."""
    lp = env["target"].parent / ".foo.md.lock"
    lp.write_text(f"999999 {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")  # dead pid → stale
    seen = lp.read_text()
    # Another process wins the race: breaks the stale lock and holds a live one.
    lp.unlink()
    live = f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')} other-writer\n"
    lp.write_text(live)
    locks._break_stale(lp, seen)
    assert lp.exists() and lp.read_text() == live
