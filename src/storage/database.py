"""
Async engine + session factory.

Production uses postgresql+asyncpg via DATABASE_URL. The test suite swaps in
sqlite+aiosqlite (see tests/conftest.py) so unit tests never require a live
Postgres instance -- the ORM models are engine-portable (see PortableJSONB /
PortableUUID in models.py).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import get_settings


def build_engine(database_url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
        )
    return create_async_engine(url, **kwargs)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str | None = None) -> AsyncEngine:
    global _engine, _session_factory
    _engine = build_engine(database_url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    """Release the process-global engine's connection pool.

    Without this, the pool keeps its connections (and, for aiosqlite, a
    *non-daemon* connection worker thread) alive, so the interpreter never
    reaches exit after `asyncio.run()` returns. Idempotent: safe to call when
    no engine was ever created, and safe to call twice.
    """
    global _engine, _session_factory
    engine = _engine
    _engine = None
    _session_factory = None
    if engine is not None:
        await engine.dispose()


@asynccontextmanager
async def engine_scope(database_url: str | None = None):
    """Own an engine for the duration of a run, disposing it on the way out.

    This is the lifecycle boundary for a CLI invocation: whoever creates the
    engine is responsible for tearing its pool down, including on failure.
    """
    engine = init_engine(database_url)
    try:
        yield engine
    finally:
        await dispose_engine()


@asynccontextmanager
async def session_scope():
    """Provide a transactional scope; commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
