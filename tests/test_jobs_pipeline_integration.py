"""
Jobs pipeline integration tests (Phase 7).

Validates end-to-end pipeline orchestration with all five jobs source
adapters using mocked fixtures. Tests cover:
- All five adapters are initialized and executed
- Stats aggregation across multiple adapters
- Validation gate behavior
- Freshness filtering
- Persistence and deduplication
- Provenance tracking (raw_document_id linkage)

All tests use mocked fixtures.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.crawlers.base import DiscoveredUrl, ParsedRecord
from src.crawlers.http import FetchResult
from src.pipeline.jobs import run_jobs_pipeline
from src.storage.models import Job, ProcessingError, RawDocument


@pytest.fixture
def reference_time() -> datetime:
    """Fixed reference time for deterministic freshness tests."""
    return datetime(2026, 8, 10, 16, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_pipeline_runs_all_five_adapters(db_session: AsyncSession, reference_time: datetime):
    """Verify pipeline initializes and runs all five job source adapters."""
    with patch("src.pipeline.jobs.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        mock_run_adapter.return_value = (RunStats(), [])

        result = await run_jobs_pipeline(
            db_session,
            target=100,
            max_concurrency=10,
            reference_time=reference_time,
        )

        assert mock_run_adapter.call_count == 5
        assert len(result.by_source) == 5
        assert "remoteok_ai_jobs" in result.by_source
        assert "workingnomads_ai_jobs" in result.by_source
        assert "ycombinator_hn_whoishiring" in result.by_source
        assert "wellfound_ai_jobs" in result.by_source
        assert "builtin_ai_jobs" in result.by_source


@pytest.mark.asyncio
async def test_pipeline_aggregates_stats(db_session: AsyncSession, reference_time: datetime):
    """Verify pipeline aggregates stats from multiple adapters."""
    with patch("src.pipeline.jobs.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        mock_run_adapter.side_effect = [
            (RunStats(discovered=10, fetched=8, parsed_records=7), []),
            (RunStats(discovered=15, fetched=12, parsed_records=10), []),
            (RunStats(discovered=5, fetched=5, parsed_records=4), []),
            (RunStats(discovered=20, fetched=18, parsed_records=15), []),
            (RunStats(discovered=8, fetched=7, parsed_records=6), []),
        ]

        result = await run_jobs_pipeline(
            db_session,
            target=100,
            max_concurrency=10,
            reference_time=reference_time,
        )

        assert result.discovered == 58
        assert result.fetched == 50


@pytest.mark.asyncio
async def test_pipeline_end_to_end_behavior(db_session: AsyncSession, reference_time: datetime):
    """
    Test end-to-end processing of valid, invalid, stale, and duplicate records.
    Validates provenance linkage.
    """
    valid_data = {
        "title": "Data Scientist",
        "company": "AI Corp",
        "url": "https://example.com/jobs/1",
        "posted_at": reference_time - timedelta(hours=2),
        "metadata_json": {
            "raw_record": {"date": (reference_time - timedelta(hours=2)).isoformat()},
            "date_field": "date", "date_value": (reference_time - timedelta(hours=2)).isoformat(),
        },
    }

    invalid_data = {
        "title": "Missing Fields",
        # missing company, url, posted_at
    }

    stale_data = {
        "title": "Old Job",
        "company": "Old Corp",
        "url": "https://example.com/jobs/old",
        "posted_at": reference_time - timedelta(days=10),
    }

    future_data = {
        "title": "Future Job",
        "company": "Future Corp",
        "url": "https://example.com/jobs/future",
        "posted_at": reference_time + timedelta(hours=10),
    }

    records = [
        ParsedRecord(
            record_type="job",
            data=valid_data,
            source_name="remoteok_ai_jobs",
            source_url="https://example.com/jobs/1",
            fetch_result=FetchResult("https://example.com/jobs/1", 200, "html", "hash1", {}),
        ),
        ParsedRecord(
            record_type="job",
            data=valid_data,  # Duplicate
            source_name="remoteok_ai_jobs",
            source_url="https://example.com/jobs/1",
            fetch_result=FetchResult("https://example.com/jobs/1", 200, "html", "hash1", {}),
        ),
        ParsedRecord(
            record_type="job",
            data=invalid_data,
            source_name="remoteok_ai_jobs",
            source_url="https://example.com/jobs/invalid",
            fetch_result=FetchResult("https://example.com/jobs/invalid", 200, "html", "hash2", {}),
        ),
        ParsedRecord(
            record_type="job",
            data=stale_data,
            source_name="remoteok_ai_jobs",
            source_url="https://example.com/jobs/old",
            fetch_result=FetchResult("https://example.com/jobs/old", 200, "html", "hash3", {}),
        ),
        ParsedRecord(
            record_type="job",
            data=future_data,
            source_name="remoteok_ai_jobs",
            source_url="https://example.com/jobs/future",
            fetch_result=FetchResult("https://example.com/jobs/future", 200, "html", "hash4", {}),
        ),
    ]

    with patch("src.pipeline.jobs.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats

        # First adapter returns the records, others return empty
        mock_run_adapter.side_effect = [
            (RunStats(discovered=5, fetched=5, parsed_records=5), records),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        result = await run_jobs_pipeline(
            db_session,
            target=100,
            reference_time=reference_time,
        )

        assert result.parsed == 5
        assert result.valid_records == 1
        assert result.duplicates == 1
        assert result.invalid_records == 1
        assert result.stale_records == 1
        assert result.future_dated_records == 1

        # Check provenance: The one valid Job should link to a RawDocument
        job_stmt = select(Job).where(Job.url == "https://example.com/jobs/1")
        job = (await db_session.execute(job_stmt)).scalar_one()

        assert job.title == "Data Scientist"
        assert job.raw_document_id is not None

        raw_stmt = select(RawDocument).where(RawDocument.id == job.raw_document_id)
        raw_doc = (await db_session.execute(raw_stmt)).scalar_one()
        assert raw_doc.source_url == "https://example.com/jobs/1"
        assert raw_doc.content_hash == hashlib.sha256(b"html").hexdigest()
        assert Path(raw_doc.raw_content_location).read_text() == "html"
        assert job.metadata_json["source_url"] == job.url

        # Check processing errors
        error_stmt = select(ProcessingError)
        errors = (await db_session.execute(error_stmt)).scalars().all()
        # Invalid records generate a processing error.
        assert len(errors) == 1
        assert "validation_error" in errors[0].error_category

        # Check DB count
        all_jobs_stmt = select(Job)
        all_jobs = (await db_session.execute(all_jobs_stmt)).scalars().all()
        assert len(all_jobs) == 1  # Deduplicated

@pytest.mark.asyncio
async def test_pipeline_adapter_isolation(db_session: AsyncSession, reference_time: datetime):
    """Verify that one failing adapter doesn't crash the whole pipeline."""
    with patch("src.pipeline.jobs.run_adapter") as mock_run_adapter:
        from src.pipeline.workers import RunStats
        
        valid_data = {
            "title": "Data Scientist",
            "company": "AI Corp",
            "url": "https://example.com/jobs/1",
            "posted_at": reference_time - timedelta(hours=2),
        "metadata_json": {
            "raw_record": {"pub_date": (reference_time - timedelta(hours=2)).isoformat()},
            "date_field": "pub_date", "date_value": (reference_time - timedelta(hours=2)).isoformat(),
        },
        }

        # Adapter 1 raises exception, adapter 2 returns a valid record, others empty
        mock_run_adapter.side_effect = [
            Exception("Adapter failed"),
            (
                RunStats(discovered=1, fetched=1, parsed_records=1), 
                [ParsedRecord(
                    record_type="job",
                    data=valid_data,
                    source_name="workingnomads_ai_jobs",
                    source_url="https://example.com/jobs/1",
                    fetch_result=FetchResult("https://example.com/jobs/1", 200, "html", "hash1", {}),
                )]
            ),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]

        try:
            result = await run_jobs_pipeline(
                db_session,
                target=100,
                reference_time=reference_time,
            )
        except Exception:
            pytest.fail("Pipeline crashed due to adapter failure")

        # The pipeline caught the exception and continued with other adapters
        assert result.valid_records == 1
