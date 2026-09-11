"""
Database wiring for the dashboard API.

This module owns no schema of its own. It reuses the ingestion pipeline's
engine factory (``src.storage.database``) and ORM models, so the dashboard can
never drift from the tables the pipeline actually writes.

Sessions are opened read-only in practice: every dashboard query is a SELECT
and no route commits. The FastAPI dependency below yields a session and always
closes it, so a failed request cannot leak a pooled connection.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.database import get_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession bound to DATABASE_URL.

    Tests override this dependency with an in-memory SQLite session (matching
    the convention already used by ``tests/conftest.py``), so the API test
    suite never requires a live Postgres instance.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            # Read-only usage: discard any accidental state rather than commit.
            await session.rollback()
