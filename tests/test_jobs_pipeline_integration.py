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


def _evidenced_record(posted_at, *, url="https://example.com/jobs/proof", source="remoteok_ai_jobs"):
    field = "pub_date" if source == "workingnomads_ai_jobs" else "date"
    raw_date = posted_at.isoformat() if isinstance(posted_at, datetime) else posted_at
    raw = {"position": "AI Engineer", "company": "Fixture Employer", "url": url, field: raw_date}
    return ParsedRecord(
        record_type="job", source_name=source, source_url=url,
        data={"title": raw["position"], "company": raw["company"], "url": url, "posted_at": posted_at,
              "metadata_json": {"raw_record": raw, "date_field": field, "date_value": raw_date}},
        fetch_result=FetchResult("https://example.com/api/jobs", 200, json.dumps([raw]), "fixture", {}),
    )


async def _run_records(session, reference, records, **kwargs):
    from src.pipeline.workers import RunStats

    async def run(adapter, **_):
        selected = [record for record in records if record.source_name == adapter.name]
        return RunStats(discovered=1, fetched=1, parsed_records=len(selected)), selected

    with patch("src.pipeline.jobs.run_adapter", side_effect=run):
        return await run_jobs_pipeline(session, reference_time=reference, **kwargs)


@pytest.mark.parametrize("age,accepted,category", [
    (timedelta(0), True, None),
    (timedelta(hours=24), True, None),
    (timedelta(hours=24, microseconds=1), False, "stale_records"),
    (-timedelta(microseconds=1), False, "future_dated_records"),
    (-timedelta(seconds=30), False, "future_dated_records"),
])
async def test_strict_24h_boundaries_override_loose_settings(db_session, reference_time, age, accepted, category):
    from src.config.settings import get_settings
    settings = get_settings().model_copy(update={"freshness_window_hours": 168, "clock_skew_tolerance_seconds": 300})
    with patch("src.pipeline.jobs.get_settings", return_value=settings):
        result = await _run_records(db_session, reference_time, [_evidenced_record(reference_time - age)])
    assert result.valid_records == int(accepted)
    jobs = (await db_session.execute(select(Job))).scalars().all()
    raw = (await db_session.execute(select(RawDocument))).scalars().all()
    assert len(jobs) == len(raw) == int(accepted)
    if category:
        assert getattr(result, category) == 1
    if jobs:
        # SQLite drops timezone labels; the written values are normalized UTC.
        assert jobs[0].collected_at == reference_time.replace(tzinfo=None)
        assert timedelta(0) <= jobs[0].collected_at - jobs[0].posted_at <= timedelta(hours=24)
        assert jobs[0].metadata_json["collected_at"] == reference_time.isoformat()


@pytest.mark.parametrize("timestamp", [None, "", "not a date", "2026-08-10", "08/10/2026",
    "yesterday", "2026-08-10T14:00:00", "2026-08-10T14:00:00-00:00",
    "2026-02-30T14:00:00Z", 1786370400, True, [], {}, datetime(2026, 8, 10, 14)])
async def test_invalid_missing_ambiguous_dates_never_persist(db_session, reference_time, timestamp):
    result = await _run_records(db_session, reference_time, [_evidenced_record(timestamp)])
    assert result.valid_records == 0
    assert result.invalid_records == 1
    assert not (await db_session.execute(select(Job))).scalars().all()
    assert not (await db_session.execute(select(RawDocument))).scalars().all()


async def test_timezone_normalization_and_full_provenance(db_session, reference_time):
    record = _evidenced_record("2026-08-10T19:30:00+05:30")
    result = await _run_records(db_session, reference_time, [record])
    assert result.valid_records == 1
    job = (await db_session.execute(select(Job))).scalar_one()
    raw = (await db_session.execute(select(RawDocument))).scalar_one()
    assert job.posted_at == datetime(2026, 8, 10, 14)
    assert job.source_name == record.source_name
    assert job.raw_document_id == raw.id
    assert job.metadata_json["source_url"] == record.source_url
    assert job.metadata_json["fetch_url"] == record.fetch_result.url == raw.source_url
    assert job.metadata_json["raw_record"] == json.loads(record.fetch_result.text)[0]
    assert Path(raw.raw_content_location).read_text() == record.fetch_result.text
    assert hashlib.sha256(Path(raw.raw_content_location).read_bytes()).hexdigest() == raw.content_hash


@pytest.mark.parametrize("fault", ["no_fetch", "blocked_fetch", "empty_body", "missing_source_url", "no_raw_item", "update_date", "mismatched_date"])
async def test_missing_or_mismatched_provenance_rejected(db_session, reference_time, fault):
    record = _evidenced_record(reference_time - timedelta(hours=1))
    if fault == "no_fetch":
        record.fetch_result = None
    elif fault == "blocked_fetch":
        record.fetch_result.status_code = 403
    elif fault == "empty_body":
        record.fetch_result.text = ""
    elif fault == "missing_source_url":
        record.source_url = ""
    elif fault == "no_raw_item":
        record.data["metadata_json"].pop("raw_record")
    elif fault == "update_date":
        record.data["metadata_json"]["date_field"] = "updated_at"
    else:
        record.data["metadata_json"]["date_value"] = reference_time.isoformat()
    result = await _run_records(db_session, reference_time, [record])
    assert result.valid_records == 0
    assert result.invalid_records == 1
    assert not (await db_session.execute(select(Job))).scalars().all()


async def test_dedup_across_sources_runs_and_tracking_urls(db_session, reference_time):
    one = _evidenced_record(reference_time)
    duplicate = _evidenced_record(reference_time, url=one.source_url + "?utm_source=duplicate")
    other = _evidenced_record(reference_time, source="workingnomads_ai_jobs")
    result = await _run_records(db_session, reference_time, [other, duplicate, one])
    assert result.valid_records == 1
    assert result.duplicates == 2
    assert result.by_source["remoteok_ai_jobs"] == 1
    assert result.by_source["workingnomads_ai_jobs"] == 0
    result2 = await _run_records(db_session, reference_time, [other, one])
    assert result2.valid_records == 0
    assert result2.duplicates == 2
    assert len((await db_session.execute(select(Job))).scalars().all()) == 1


@pytest.mark.parametrize("disable_all", [False, True])
async def test_disabled_sources_never_invoked(db_session, reference_time, monkeypatch, disable_all):
    from src.config.sources import SOURCE_REGISTRY, Vertical
    from src.pipeline.workers import RunStats
    for source in SOURCE_REGISTRY:
        if source.vertical == Vertical.JOBS and (disable_all or source.name == "remoteok_ai_jobs"):
            monkeypatch.setattr(source, "enabled", False)
    with patch("src.pipeline.jobs.run_adapter", return_value=(RunStats(), [])) as run:
        result = await run_jobs_pipeline(db_session, reference_time=reference_time)
    assert run.call_count == (0 if disable_all else 4)
    assert "remoteok_ai_jobs" not in result.by_source
    assert result.valid_records == 0


async def test_five_registered_sources_match_executable_adapters():
    from src.config.sources import Vertical, get_sources_for_vertical
    from src.pipeline.jobs import ADAPTER_CLASSES
    assert len(ADAPTER_CLASSES) == 5
    assert {c.name for c in ADAPTER_CLASSES} == {s.name for s in get_sources_for_vertical(Vertical.JOBS)}


@pytest.mark.parametrize("target", [0, 1, 1000])
async def test_target_is_ceiling_and_never_padding(db_session, reference_time, target):
    records = [_evidenced_record(reference_time, url=f"https://example.com/jobs/{i}") for i in range(2)]
    result = await _run_records(db_session, reference_time, records, target=target)
    assert result.valid_records == min(target, 2)
    assert len((await db_session.execute(select(Job))).scalars().all()) == min(target, 2)


async def test_all_failures_and_empty_sources_never_fabricate(db_session, reference_time):
    from src.errors import BlockedSourceError
    from src.pipeline.workers import RunStats
    with patch("src.pipeline.jobs.run_adapter", side_effect=[
        BlockedSourceError("403"), RuntimeError("timeout"),
        (RunStats(fetch_failed=1, errors=["invalid JSON"]), []),
        (RunStats(blocked=1), []), (RunStats(), []),
    ]) as run:
        result = await run_jobs_pipeline(db_session, target=1000, reference_time=reference_time)
    assert run.call_count == 5
    assert result.valid_records == 0
    assert result.blocked == 2
    assert result.fetch_failed == 2
    assert result.by_source == dict.fromkeys(result.by_source, 0)
    assert len((await db_session.execute(select(ProcessingError))).scalars().all()) == 4
    assert not (await db_session.execute(select(Job))).scalars().all()
    assert not (await db_session.execute(select(RawDocument))).scalars().all()


async def test_naive_reference_time_is_not_assumed_utc(db_session):
    with pytest.raises(ValueError, match="timezone-aware"):
        await run_jobs_pipeline(db_session, reference_time=datetime(2026, 8, 10))
