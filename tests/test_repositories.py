from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.storage.database import build_engine
from src.storage.models import Base
from src.storage.repositories import NewsRepository


@pytest.mark.asyncio
async def test_upsert_returns_false_on_duplicate(db_session):
    repo = NewsRepository(db_session)
    inserted = await repo.upsert(
        title="A", url="https://x.com/1", source_name="hackernews_ai", published_at=datetime.now(timezone.utc)
    )
    assert inserted is True

    inserted_again = await repo.upsert(
        title="A (different title, same url)",
        url="https://x.com/1",
        source_name="hackernews_ai",
        published_at=datetime.now(timezone.utc),
    )
    assert inserted_again is False

    assert await repo.exists("hackernews_ai", "https://x.com/1") is True


@pytest.mark.asyncio
async def test_concurrent_workers_racing_same_url_produce_one_row():
    """Two 'workers' (separate sessions) discover the same URL at the same
    time. Section 29: this must remain safe without relying on in-memory
    locking. Only one row should ever exist afterward.
    """
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def worker():
        async with factory() as session:
            repo = NewsRepository(session)
            return await repo.upsert(
                title="Race",
                url="https://race.com/article",
                source_name="hackernews_ai",
                published_at=datetime.now(timezone.utc),
            )

    results = await asyncio.gather(*(worker() for _ in range(10)))
    # Exactly one worker should have won the insert; the rest are no-ops.
    assert sum(1 for r in results if r is True) == 1

    async with factory() as session:
        from sqlalchemy import select, func
        from src.storage.models import News

        count = (await session.execute(select(func.count()).select_from(News))).scalar_one()
        assert count == 1

    await engine.dispose()
