"""
Regression tests for the CLI/process-exit lifecycle bug.

Symptom (pre-existing): `python -m src.main --vertical research` finished its
work, logged its summary, and then hung until killed (`timeout` exit 124).

Cause: the process-global async engine created by `init_engine()` was never
disposed, so its connection pool stayed checked out. Under aiosqlite that pool
owns a **non-daemon** `_connection_worker_thread`, and a non-daemon thread
blocks interpreter shutdown after `asyncio.run()` returns.

Fix: `engine_scope()` owns the engine for the duration of a run and disposes it
on exit (including on failure); `src.main`'s vertical runners use it.

These tests assert the lifecycle contract -- no timeouts, no daemon-thread
tricks, no swallowed exceptions -- plus one end-to-end subprocess test that the
CLI process actually terminates on its own.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from src.storage import database as db
from src.storage.database import dispose_engine, engine_scope, init_engine
from src.storage.models import Base

SQLITE_MEMORY = "sqlite+aiosqlite:///:memory:"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _pool_threads() -> set[threading.Thread]:
    """Non-daemon worker threads that would block interpreter shutdown."""
    return {
        t
        for t in threading.enumerate()
        if t is not threading.main_thread() and not t.daemon and "_connection_worker_thread" in t.name
    }


@pytest.mark.asyncio
async def test_engine_scope_disposes_pool_and_leaves_no_blocking_thread():
    before = _pool_threads()

    async with engine_scope(SQLITE_MEMORY) as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # The pool is live inside the scope: a worker thread exists.
        assert _pool_threads() - before, "expected a live connection worker thread inside the scope"

    # Leaving the scope must dispose the pool, retiring its worker thread.
    for t in _pool_threads() - before:
        t.join(timeout=5)
    assert not (_pool_threads() - before), "connection worker thread survived engine_scope"


@pytest.mark.asyncio
async def test_engine_scope_disposes_even_when_body_raises():
    """Cleanup must happen on the failure path too -- and the error must propagate."""
    before = _pool_threads()

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        async with engine_scope(SQLITE_MEMORY) as engine:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            raise Boom("pipeline failed")

    for t in _pool_threads() - before:
        t.join(timeout=5)
    assert not (_pool_threads() - before), "pool leaked when the run raised"


@pytest.mark.asyncio
async def test_engine_scope_clears_global_engine_state():
    async with engine_scope(SQLITE_MEMORY):
        assert db._engine is not None
        assert db._session_factory is not None
    assert db._engine is None
    assert db._session_factory is None


@pytest.mark.asyncio
async def test_dispose_engine_is_idempotent():
    init_engine(SQLITE_MEMORY)
    await dispose_engine()
    await dispose_engine()  # second call must not raise
    assert db._engine is None


def test_main_uses_engine_scope_not_bare_init_engine():
    """Guard against a future edit reintroducing an undisposed engine in the CLI."""
    source = (REPO_ROOT / "src" / "main.py").read_text()
    assert "engine_scope" in source
    assert "init_engine()" not in source, "src/main.py must not create an engine it never disposes"


def test_cli_process_exits_on_its_own():
    """End-to-end: the CLI process must terminate without being killed.

    Uses --dry-run so no network calls or DB writes happen; the CLI still runs
    its full startup/shutdown path. `subprocess.run` with a wall-clock guard is
    the assertion mechanism (the test fails if the process must be killed), not
    a timeout papering over the bug.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--vertical", "research", "--dry-run"],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": SQLITE_MEMORY, "HOME": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"CLI exited {proc.returncode}\n{proc.stderr[-2000:]}"
