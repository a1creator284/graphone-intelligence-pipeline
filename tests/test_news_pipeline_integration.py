"""
News pipeline integration tests (Phase 6, Task 9.4 -- requirements.md
Requirement 16.1, 16.11).

Validates end-to-end pipeline orchestration with all five news source
adapters using mocked fixtures. Tests cover:
- All five adapters are initialized and executed
- Stats aggregation across multiple adapters
- Mixed success and failure handling (some succeed, some fail, pipeline continues)
- Validation gate behavior
- Freshness filtering
- Persistence and deduplication

All tests use mocked fixtures (no live network access per Requirement 18.1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.crawlers.base import DiscoveredUrl, ParsedRecord
from src.crawlers.http import FetchResult
from src.pipeline.news import run_news_pipeline
from src.storage.models import News, ProcessingError, RawDocument

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def reference_time() -> datetime:
    """Fixed reference time for deterministic freshness tests."""
    return datetime(2026, 8, 10, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def techcrunch_rss_feed() -> str:
    return (FIXTURES_DIR / "techcrunch_ai_sample.xml").read_text()


@pytest.fixture
def sample_article_techcrunch() -> str:
    return (FIXTURES_DIR / "sample_article_techcrunch.html").read_text()


@pytest.mark.asyncio
async def test_pipeline_runs_all_five_adapters(db_session: AsyncSession, reference_time: datetime):
    """Verify pipeline initializes and runs all five news source adapters."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        # Mock run_adapter to return empty stats for each adapter
        from src.pipeline.workers import RunStats

        mock_run_adapter.return_value = (RunStats(), [])

        result = await run_news_pipeline(
            db_session,
            target=100,
            max_concurrency=10,
            reference_time=reference_time,
        )

        # Verify run_adapter was called 5 times (once per adapter)
        assert mock_run_adapter.call_count == 5

        # Verify all five adapters are in by_source
        assert len(result.by_source) == 5
        assert "hackernews_ai" in result.by_source
        assert "techcrunch_ai_rss" in result.by_source
        assert "theverge_ai_rss" in result.by_source
        assert "mit_technology_review_ai_rss" in result.by_source
        assert "synced_review_rss" in result.by_source


@pytest.mark.asyncio
async def test_pipeline_aggregates_stats_across_adapters(db_session: AsyncSession, reference_time: datetime):
    """Verify pipeline aggregates discovery/fetch/parse stats from multiple adapters."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        # Mock different stats for each adapter
        mock_run_adapter.side_effect = [
            (RunStats(discovered=10, fetched=8, parsed_records=7), []),
            (RunStats(discovered=15, fetched=12, parsed_records=10), []),
            (RunStats(discovered=5, fetched=5, parsed_records=4), []),
            (RunStats(discovered=20, fetched=18, parsed_records=15), []),
            (RunStats(discovered=8, fetched=7, parsed_records=6), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=100,
            max_concurrency=10,
            reference_time=reference_time,
        )

        # Verify aggregated stats
        assert result.discovered == 10 + 15 + 5 + 20 + 8
        assert result.fetched == 8 + 12 + 5 + 18 + 7
        # parsed is not tracked in NewsPipelineResult (only valid_records)


@pytest.mark.asyncio
async def test_pipeline_validates_and_persists_valid_records(
    db_session: AsyncSession,
    reference_time: datetime,
    sample_article_techcrunch: str,
):
    """Verify pipeline validates records and persists to database."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        # Create a mock ParsedRecord with valid data
        fetch_result = FetchResult(
            url="https://techcrunch.com/2026/08/10/test-article/",
            status_code=200,
            text=sample_article_techcrunch,
            content_hash="abc123",
            headers={},
        )

        parsed_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Test AI Article",
                "url": "https://techcrunch.com/2026/08/10/test-article/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=2),  # Fresh (within 24h)
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {
                    "full_text": "This is a test article with enough content to pass the minimum length requirement. " * 5,
                    "truncated": False,
                    "extraction_library": "trafilatura",
                },
                "publication_date_candidates": {
                    "rss_pubDate": "Mon, 10 Aug 2026 14:00:00 +0000",
                    "selected": "rss_pubDate",
                },
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/2026/08/10/test-article/",
            fetch_result=fetch_result,
        )

        mock_run_adapter.side_effect = [
            (RunStats(discovered=1, fetched=1, parsed_records=1), [parsed_record]),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        # Verify stats
        assert result.valid_records == 1
        assert result.duplicates == 0
        assert result.invalid_records == 0
        assert result.stale_records == 0
        assert result.future_dated_records == 0

        # Verify database persistence
        news_records = (await db_session.execute(select(News))).scalars().all()
        assert len(news_records) == 1
        assert news_records[0].title == "Test AI Article"
        assert news_records[0].source_name == "techcrunch_ai_rss"

        # Verify raw document provenance
        raw_docs = (await db_session.execute(select(RawDocument))).scalars().all()
        assert len(raw_docs) == 1
        assert raw_docs[0].content_hash == "abc123"


@pytest.mark.asyncio
async def test_pipeline_handles_mixed_success_and_failure(
    db_session: AsyncSession,
    reference_time: datetime,
    sample_article_techcrunch: str,
):
    """Verify pipeline continues processing when some records succeed and some fail."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        fetch_result = FetchResult(
            url="https://techcrunch.com/test/",
            status_code=200,
            text=sample_article_techcrunch,
            content_hash="hash1",
            headers={},
        )

        # Valid record (fresh)
        valid_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Valid Article",
                "url": "https://techcrunch.com/valid/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=1),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Valid content " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/valid/",
            fetch_result=fetch_result,
        )

        # Stale record (> 24 hours old)
        stale_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Stale Article",
                "url": "https://techcrunch.com/stale/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=25),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Stale content " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/stale/",
            fetch_result=fetch_result,
        )

        # Invalid record (missing title)
        invalid_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "",  # Invalid: empty title
                "url": "https://techcrunch.com/invalid/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=1),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Invalid content " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/invalid/",
            fetch_result=fetch_result,
        )

        # Future-dated record
        future_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Future Article",
                "url": "https://techcrunch.com/future/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time + timedelta(minutes=10),  # Beyond clock skew
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Future content " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/future/",
            fetch_result=fetch_result,
        )

        mock_run_adapter.side_effect = [
            (RunStats(discovered=4, fetched=4, parsed_records=4), [valid_record, stale_record, invalid_record, future_record]),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        # Verify different rejection categories
        assert result.valid_records == 1  # Only valid_record persisted
        assert result.stale_records == 1  # stale_record rejected
        assert result.invalid_records == 1  # invalid_record rejected
        assert result.future_dated_records == 1  # future_record rejected

        # Verify only valid record was persisted
        news_records = (await db_session.execute(select(News))).scalars().all()
        assert len(news_records) == 1
        assert news_records[0].title == "Valid Article"

        # Verify errors logged
        errors = (await db_session.execute(select(ProcessingError))).scalars().all()
        assert len(errors) == 1  # Only validation error logged
        assert errors[0].error_category == "validation_error"


@pytest.mark.asyncio
async def test_pipeline_deduplicates_by_url(
    db_session: AsyncSession,
    reference_time: datetime,
    sample_article_techcrunch: str,
):
    """Verify pipeline deduplicates articles by (source_name, url)."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        fetch_result = FetchResult(
            url="https://techcrunch.com/test/",
            status_code=200,
            text=sample_article_techcrunch,
            content_hash="hash1",
            headers={},
        )

        # Same URL, same source (should deduplicate)
        record1 = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Article 1",
                "url": "https://techcrunch.com/same-url/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=1),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Content 1 " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/same-url/",
            fetch_result=fetch_result,
        )

        record2 = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Article 2 (duplicate)",
                "url": "https://techcrunch.com/same-url/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=2),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Content 2 " * 20},
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/same-url/",
            fetch_result=fetch_result,
        )

        mock_run_adapter.side_effect = [
            (RunStats(discovered=2, fetched=2, parsed_records=2), [record1, record2]),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        # Verify deduplication
        assert result.valid_records == 1  # First record inserted
        assert result.duplicates == 1  # Second record deduplicated

        # Verify only one record in database
        news_records = (await db_session.execute(select(News))).scalars().all()
        assert len(news_records) == 1
        assert news_records[0].title == "Article 1"  # First one wins


@pytest.mark.asyncio
async def test_pipeline_handles_extraction_failed_records(
    db_session: AsyncSession,
    reference_time: datetime,
):
    """Verify pipeline correctly handles records with extraction_failed flag."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        fetch_result = FetchResult(
            url="https://techcrunch.com/test/",
            status_code=200,
            text="<html><body>Too short</body></html>",
            content_hash="hash1",
            headers={},
        )

        # Record with extraction_failed flag (returned by adapters when ArticleExtractor fails)
        failed_record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Failed Extraction",
                "url": "https://techcrunch.com/failed/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=1),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {
                    "extraction_failed": True,  # This flag indicates extraction failure
                },
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/failed/",
            fetch_result=fetch_result,
        )

        mock_run_adapter.side_effect = [
            (RunStats(discovered=1, fetched=1, parsed_records=0), [failed_record]),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        # Verify extraction failure counted
        assert result.extraction_failed == 1
        assert result.valid_records == 0

        # Verify no record persisted to News table
        news_records = (await db_session.execute(select(News))).scalars().all()
        assert len(news_records) == 0


@pytest.mark.asyncio
async def test_pipeline_persists_raw_documents_for_provenance(
    db_session: AsyncSession,
    reference_time: datetime,
    sample_article_techcrunch: str,
):
    """Verify pipeline persists raw HTML for provenance before structured records."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        fetch_result = FetchResult(
            url="https://techcrunch.com/test/",
            status_code=200,
            text=sample_article_techcrunch,
            content_hash="provenance_hash_123",
            headers={},
        )

        record = ParsedRecord(
            record_type="NEWS",
            data={
                "title": "Provenance Test",
                "url": "https://techcrunch.com/provenance/",
                "source_name": "techcrunch_ai_rss",
                "published_at": reference_time - timedelta(hours=1),
                "full_text_location": "inline:extracted_metadata.full_text",
                "extracted_metadata": {"full_text": "Provenance content " * 20},
                "publication_date_candidates": {
                    "rss_pubDate": "Mon, 10 Aug 2026 15:00:00 +0000",
                },
            },
            source_name="techcrunch_ai_rss",
            source_url="https://techcrunch.com/provenance/",
            fetch_result=fetch_result,
        )

        mock_run_adapter.side_effect = [
            (RunStats(discovered=1, fetched=1, parsed_records=1), [record]),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        # Verify raw document was created
        raw_docs = (await db_session.execute(select(RawDocument))).scalars().all()
        assert len(raw_docs) == 1
        assert raw_docs[0].content_hash == "provenance_hash_123"
        assert raw_docs[0].source_name == "techcrunch_ai_rss"
        assert raw_docs[0].extraction_status == "extracted"

        # Verify news record references raw document
        news_records = (await db_session.execute(select(News))).scalars().all()
        assert len(news_records) == 1
        assert news_records[0].raw_document_id == raw_docs[0].id


@pytest.mark.asyncio
async def test_pipeline_splits_target_across_adapters(db_session: AsyncSession, reference_time: datetime):
    """Verify pipeline splits target evenly across all five adapters."""
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        mock_run_adapter.return_value = (RunStats(), [])

        await run_news_pipeline(
            db_session,
            target=100,
            max_concurrency=10,
            reference_time=reference_time,
        )

        # Verify each adapter was called with per_adapter_target = 100 // 5 = 20
        assert mock_run_adapter.call_count == 5
        for call in mock_run_adapter.call_args_list:
            # Check max_items parameter
            assert call.kwargs["max_items"] == 20


@pytest.mark.asyncio
async def test_pipeline_logs_all_required_events(db_session: AsyncSession, reference_time: datetime, capsys):
    """Verify pipeline logs all required structured log events.

    Note: structlog uses PrintLoggerFactory (stdout), not stdlib logging,
    so we capture stdout via capsys rather than caplog.
    """
    with patch("src.pipeline.news.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        mock_run_adapter.return_value = (RunStats(), [])

        await run_news_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

        captured = capsys.readouterr()
        stdout = captured.out

        # Verify pipeline_start log
        assert "pipeline_start" in stdout

        # Verify adapter_complete logs (5 times, one per adapter)
        assert stdout.count("adapter_complete") == 5

        # Verify pipeline_complete log
        assert "pipeline_complete" in stdout
