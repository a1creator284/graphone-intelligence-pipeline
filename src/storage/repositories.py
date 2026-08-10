"""
Repository layer: idempotent upserts on top of the DB-level unique
constraints defined in models.py (Section 22 requirement).

Every write here uses Postgres/SQLite-compatible "INSERT ... ON CONFLICT DO
NOTHING/UPDATE" semantics via SQLAlchemy's dialect-aware insert(), so two
workers racing on the same natural key never produce two rows or raise --
the second writer's insert becomes a no-op (or a refresh of mutable fields).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import EntityMappingLog, News, ProcessingError, RawDocument, ResearchPaper, Startup


def _insert_for(session: AsyncSession):
    """Return the dialect-appropriate insert() builder that supports
    on_conflict_do_nothing/on_conflict_do_update."""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if dialect == "postgresql" else sqlite_insert


class NewsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, **fields) -> bool:
        """Insert a news record if (source_name, url) hasn't been seen.
        Returns True if a new row was inserted, False if it was a duplicate.
        """
        insert_ = _insert_for(self.session)
        stmt = insert_(News).values(**fields).on_conflict_do_nothing(
            index_elements=["source_name", "url"]
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def exists(self, source_name: str, url: str) -> bool:
        stmt = select(News.id).where(News.source_name == source_name, News.url == url)
        return (await self.session.execute(stmt)).first() is not None


class ResearchPaperRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, **fields) -> bool:
        insert_ = _insert_for(self.session)
        stmt = insert_(ResearchPaper).values(**fields).on_conflict_do_nothing(
            index_elements=["paper_url"]
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def update_github_metadata(self, paper_url: str, *, github_url: str | None, github_stars: int | None) -> None:
        stmt = select(ResearchPaper).where(ResearchPaper.paper_url == paper_url)
        paper = (await self.session.execute(stmt)).scalar_one_or_none()
        if paper is None:
            return
        paper.github_url = github_url
        paper.github_stars = github_stars
        await self.session.commit()

    async def exists(self, paper_url: str) -> bool:
        stmt = select(ResearchPaper.id).where(ResearchPaper.paper_url == paper_url)
        return (await self.session.execute(stmt)).first() is not None


class RawDocumentRepository:
    """Provenance persistence (Section 8). Deduplicated on content_hash --
    if the exact same content was already captured, the existing row's id
    is returned instead of inserting a duplicate."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, **fields) -> "RawDocument":
        content_hash = fields["content_hash"]
        existing = await self.session.execute(select(RawDocument).where(RawDocument.content_hash == content_hash))
        row = existing.scalar_one_or_none()
        if row is not None:
            return row

        insert_ = _insert_for(self.session)
        stmt = (
            insert_(RawDocument)
            .values(**fields)
            .on_conflict_do_nothing(index_elements=["content_hash"])
            .returning(RawDocument.id)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        new_id = result.scalar_one_or_none()
        if new_id is None:
            # Lost a race to another writer inserting the same content_hash
            # concurrently -- fetch the winner's row instead of erroring.
            existing = await self.session.execute(select(RawDocument).where(RawDocument.content_hash == content_hash))
            return existing.scalar_one()

        fetched = await self.session.execute(select(RawDocument).where(RawDocument.id == new_id))
        return fetched.scalar_one()


class StartupRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, **fields) -> bool:
        insert_ = _insert_for(self.session)
        stmt = insert_(Startup).values(**fields).on_conflict_do_nothing(
            index_elements=["source_name", "source_url"]
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0


class EntityMappingLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, **fields) -> EntityMappingLog:
        entry = EntityMappingLog(**fields)
        self.session.add(entry)
        await self.session.commit()
        return entry


class ProcessingErrorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, **fields) -> ProcessingError:
        entry = ProcessingError(**fields)
        self.session.add(entry)
        await self.session.commit()
        return entry
