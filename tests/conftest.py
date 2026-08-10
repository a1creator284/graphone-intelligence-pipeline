from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.storage.database import build_engine
from src.storage.models import Base


@pytest_asyncio.fixture
async def db_session():
    """Fresh in-memory sqlite DB per test -- fast, isolated, no live Postgres needed."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def frozen_now():
    from datetime import datetime, timezone

    return datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
