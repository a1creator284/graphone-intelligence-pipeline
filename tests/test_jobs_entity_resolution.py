"""
Entity resolution wiring inside the jobs pipeline (Phase 12).

Confirms that the jobs vertical actually calls the resolver, stamps
``canonical_entity_id`` on persisted jobs, and produces the audit rows that
back the required "Entity Mapping Log" export tab.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.crawlers.base import ParsedRecord
from src.crawlers.http import FetchResult
from src.pipeline.jobs import run_jobs_pipeline
from src.storage.models import CanonicalEntity, EntityMappingLog, Job


@pytest.fixture
def reference_time() -> datetime:
    return datetime(2026, 8, 10, 16, 0, 0, tzinfo=timezone.utc)


def _job_record(title: str, company: str, url: str, posted_at: datetime) -> ParsedRecord:
    raw = {"position": title, "company": company, "url": url, "date": posted_at.isoformat()}
    return ParsedRecord(
        record_type="job",
        data={"title": title, "company": company, "url": url, "posted_at": posted_at,
              "metadata_json": {"raw_record": raw, "date_field": "date", "date_value": raw["date"]}},
        source_name="remoteok_ai_jobs",
        source_url=url,
        fetch_result=FetchResult(url, 200, json.dumps(raw), "fixture", {}),
    )


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _run(db_session: AsyncSession, records: list[ParsedRecord], reference_time: datetime):
    from src.pipeline.workers import RunStats

    with patch("src.pipeline.jobs.run_adapter") as mock_run_adapter:
        mock_run_adapter.side_effect = [
            (RunStats(discovered=len(records), fetched=len(records), parsed_records=len(records)), records),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]
        return await run_jobs_pipeline(
            db_session,
            target=100,
            max_concurrency=5,
            reference_time=reference_time,
        )


@pytest.mark.asyncio
async def test_jobs_get_canonical_entity_ids(db_session: AsyncSession, reference_time: datetime) -> None:
    fresh = reference_time - timedelta(hours=1)
    records = [
        _job_record("ML Engineer", "OpenAI", "https://example.com/jobs/1", fresh),
        _job_record("Research Scientist", "OpenAI, Inc.", "https://example.com/jobs/2", fresh),
        _job_record("Data Engineer", "Cohere", "https://example.com/jobs/3", fresh),
    ]

    result = await _run(db_session, records, reference_time)

    assert result.valid_records == 3
    assert result.entities_resolved == 3
    # "OpenAI" and "OpenAI, Inc." collapse to one canonical entity.
    assert result.entities_created == 2
    assert await _count(db_session, CanonicalEntity) == 2

    jobs = (await db_session.execute(select(Job).order_by(Job.url))).scalars().all()
    assert all(j.canonical_entity_id is not None for j in jobs)
    by_url = {j.url: j.canonical_entity_id for j in jobs}
    assert by_url["https://example.com/jobs/1"] == by_url["https://example.com/jobs/2"]
    assert by_url["https://example.com/jobs/3"] != by_url["https://example.com/jobs/1"]


@pytest.mark.asyncio
async def test_mapping_log_is_populated_for_every_persisted_job(
    db_session: AsyncSession, reference_time: datetime
) -> None:
    fresh = reference_time - timedelta(hours=1)
    records = [
        _job_record("ML Engineer", "Hugging Face", "https://example.com/jobs/1", fresh),
        _job_record("MLOps", "Hugging-Face", "https://example.com/jobs/2", fresh),
    ]

    result = await _run(db_session, records, reference_time)

    logs = (await db_session.execute(select(EntityMappingLog))).scalars().all()
    assert len(logs) == 2
    assert {log.raw_name for log in logs} == {"Hugging Face", "Hugging-Face"}
    assert all(log.source_url is not None for log in logs)
    assert all(log.canonical_entity_id is not None for log in logs)
    assert result.entity_methods.get("created") == 1
    assert result.entity_methods.get("normalized_exact") == 1


@pytest.mark.asyncio
async def test_stale_records_do_not_create_entities(db_session: AsyncSession, reference_time: datetime) -> None:
    """Freshness rejection happens before resolution -- no phantom entities."""
    records = [
        _job_record("Old Job", "Ghost Corp", "https://example.com/jobs/old", reference_time - timedelta(days=10)),
    ]

    result = await _run(db_session, records, reference_time)

    assert result.stale_records == 1
    assert result.valid_records == 0
    assert await _count(db_session, CanonicalEntity) == 0
    assert await _count(db_session, EntityMappingLog) == 0


@pytest.mark.asyncio
async def test_resolution_failure_does_not_break_ingestion(
    db_session: AsyncSession, reference_time: datetime
) -> None:
    """If the resolver blows up, the job still persists with a null link."""
    fresh = reference_time - timedelta(hours=1)
    records = [_job_record("ML Engineer", "OpenAI", "https://example.com/jobs/1", fresh)]

    from src.pipeline.workers import RunStats

    with (
        patch("src.pipeline.jobs.run_adapter") as mock_run_adapter,
        patch(
            "src.resolution.resolver.EntityResolver.resolve",
            side_effect=RuntimeError("resolver exploded"),
        ),
    ):
        mock_run_adapter.side_effect = [
            (RunStats(discovered=1, fetched=1, parsed_records=1), records),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
            (RunStats(), []),
        ]
        result = await run_jobs_pipeline(
            db_session,
            target=10,
            max_concurrency=5,
            reference_time=reference_time,
        )

    assert result.valid_records == 1
    assert result.entities_unresolved == 1
    job = (await db_session.execute(select(Job))).scalar_one()
    assert job.canonical_entity_id is None
