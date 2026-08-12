"""
Section 28 requires deduplication to be enforced at the database level, not
only in application code. These tests prove the unique constraints actually
reject duplicates rather than trusting that they would.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from src.storage.models import News, ResearchPaper, Startup


@pytest.mark.asyncio
async def test_news_source_url_uniqueness_enforced_by_db(db_session):
    from datetime import datetime, timezone

    n1 = News(
        title="A",
        url="https://example.com/a",
        source_name="techcrunch_ai_rss",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(n1)
    await db_session.commit()

    n2 = News(
        title="A duplicate",
        url="https://example.com/a",
        source_name="techcrunch_ai_rss",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(n2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_research_paper_url_globally_unique(db_session):
    p1 = ResearchPaper(title="Paper 1", paper_url="https://arxiv.org/abs/1234.5678", source_name="arxiv")
    db_session.add(p1)
    await db_session.commit()

    # Even from a different source_name, the same paper_url must not duplicate.
    p2 = ResearchPaper(title="Paper 1 (mirror)", paper_url="https://arxiv.org/abs/1234.5678", source_name="papers_with_code")
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_research_paper_negative_github_stars_rejected(db_session):
    p = ResearchPaper(
        title="Bad stars",
        paper_url="https://arxiv.org/abs/9999.0001",
        source_name="arxiv",
        github_url="https://github.com/foo/bar",
        github_stars=-5,
    )
    db_session.add(p)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_startup_null_github_and_employee_count_allowed(db_session):
    """Section 23: employee_count must be nullable, never fabricated."""
    s = Startup(
        entity_name="Unknown Employee Count Co",
        source_name="ycombinator_directory",
        source_url="https://ycombinator.com/companies/example",
        employee_count=None,
    )
    db_session.add(s)
    await db_session.commit()
    assert s.employee_count is None


@pytest.mark.asyncio
async def test_startup_same_source_url_twice_rejected(db_session):
    s1 = Startup(entity_name="X", source_name="ycombinator_directory", source_url="https://x.com/1")
    db_session.add(s1)
    await db_session.commit()

    s2 = Startup(entity_name="X again", source_name="ycombinator_directory", source_url="https://x.com/1")
    db_session.add(s2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_news_same_url_different_sources_produce_separate_rows(db_session):
    from datetime import datetime, timezone
    
    n1 = News(
        title="Article from HN",
        url="https://example.com/ai-breakthrough",
        source_name="hackernews_ai",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(n1)
    await db_session.commit()

    # Same URL but different source should be allowed
    n2 = News(
        title="Article from TechCrunch",
        url="https://example.com/ai-breakthrough",
        source_name="techcrunch_ai_rss",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(n2)
    # Should not raise IntegrityError
    await db_session.commit()
    
    from sqlalchemy import select
    from sqlalchemy import func
    count = (await db_session.execute(select(func.count()).select_from(News).where(News.url == "https://example.com/ai-breakthrough"))).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_raw_document_content_hash_uniqueness_enforced_by_db(db_session):
    from src.storage.models import RawDocument
    
    rd1 = RawDocument(
        source_name="hackernews_ai",
        source_url="https://example.com/1",
        canonical_url="https://example.com/1",
        http_status=200,
        content_hash="deadbeef12345",
        extraction_status="extracted",
    )
    db_session.add(rd1)
    await db_session.commit()
    
    # Same content_hash should raise IntegrityError
    rd2 = RawDocument(
        source_name="techcrunch_ai_rss",
        source_url="https://example.com/2",
        canonical_url="https://example.com/2",
        http_status=200,
        content_hash="deadbeef12345",
        extraction_status="extracted",
    )
    db_session.add(rd2)
    
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
