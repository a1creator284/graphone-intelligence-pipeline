"""
Jobs vertical pipeline (Phase 7).

Orchestrates jobs ingestion from five sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.config.settings import get_settings


import traceback

from src.crawlers.builtin import BuiltInAIAdapter
from src.crawlers.http import AsyncHttpClient
from src.crawlers.remoteok import RemoteOKAIAdapter
from src.crawlers.wellfound import WellfoundAIAdapter
from src.crawlers.workingnomads import WorkingNomadsAIAdapter
from src.crawlers.ycombinator_jobs import YCombinatorWhoIsHiringAdapter
from src.pipeline.workers import run_adapter
from src.resolution import EntityResolver
from src.storage.repositories import JobRepository, ProcessingErrorRepository, RawDocumentRepository
from src.validation.freshness import is_fresh
from src.validation.schemas import validate_job_record

logger = get_logger(component="jobs_pipeline")

ADAPTER_CLASSES = [
    RemoteOKAIAdapter,
    WorkingNomadsAIAdapter,
    YCombinatorWhoIsHiringAdapter,
    WellfoundAIAdapter,
    BuiltInAIAdapter,
]

@dataclass(slots=True)
class JobsPipelineResult:
    """Pipeline execution result matching research pipeline pattern."""

    target: int
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    valid_records: int = 0
    duplicates: int = 0
    rejected: int = 0
    stale_records: int = 0
    future_dated_records: int = 0
    invalid_records: int = 0
    fetch_failed: int = 0
    blocked: int = 0
    entities_resolved: int = 0
    entities_created: int = 0
    entities_unresolved: int = 0
    entity_methods: dict[str, int] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)


async def run_jobs_pipeline(
    session: AsyncSession,
    *,
    target: int = 1000,
    max_concurrency: int = 20,
    reference_time: datetime | None = None,
) -> JobsPipelineResult:
    """
    Run the jobs ingestion pipeline.
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    settings = get_settings()
    result = JobsPipelineResult(target=target)

    job_repo = JobRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)
    # Entity resolution (Section 22): every job's employer name is mapped to
    # a canonical entity and the decision is written to entity_mapping_log.
    resolver = EntityResolver(session)

    logger.info(
        "pipeline_start",
        vertical="jobs",
        reference_time=reference_time.isoformat(),
        target=target,
        max_concurrency=max_concurrency,
        clock_skew_tolerance_seconds=settings.clock_skew_tolerance_seconds,
        freshness_window_hours=settings.freshness_window_hours,
    )

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        per_adapter_target = max(1, target // len(ADAPTER_CLASSES))

        adapters = [
            RemoteOKAIAdapter(http_client),
            WorkingNomadsAIAdapter(http_client),
            YCombinatorWhoIsHiringAdapter(http_client),
            WellfoundAIAdapter(http_client),
            BuiltInAIAdapter(http_client),
        ]

        all_records = []
        for adapter in adapters:
            try:
                stats, records = await run_adapter(
                    adapter,
                    max_concurrency=max_concurrency,
                    max_items=per_adapter_target,
                )

                result.discovered += stats.discovered
                result.fetched += stats.fetched
                result.fetch_failed += stats.fetch_failed
                result.blocked += stats.blocked
                result.by_source[adapter.name] = len(records)

                logger.info(
                    "adapter_complete",
                    source=adapter.name,
                    discovered=stats.discovered,
                    fetched=stats.fetched,
                    parsed_records=len(records),
                    fetch_failed=stats.fetch_failed,
                    blocked=stats.blocked,
                )

                all_records.extend(records)
            except Exception as exc:
                logger.error(
                    "adapter_failed",
                    source=adapter.name,
                    error=str(exc),
                    exc_info=True,
                )
                await error_repo.record(
                    source_name=adapter.name,
                    url="N/A",
                    error_category="AdapterError",
                    message=f"Adapter {adapter.name} failed: {exc}",
                    context={},
                )

        for record in all_records:
            data = dict(record.data)
            data["source_name"] = record.source_name
            result.parsed += 1

            validated, error = validate_job_record(data)
            if validated is None:
                result.invalid_records += 1
                result.rejection_reasons.append(f"{record.source_url}: {error}")
                logger.warning(
                    "validation_failed",
                    url=record.source_url,
                    source=record.source_name,
                    error=error,
                )
                await error_repo.record(
                    source_name=record.source_name,
                    url=record.source_url,
                    error_category="validation_error",
                    message=error or "unknown validation failure",
                    context={"record_type": record.record_type},
                )
                continue

            is_fresh_result, rejection_reason = is_fresh(
                validated.posted_at,
                reference_time,
                window_hours=settings.freshness_window_hours,
                clock_skew_tolerance_seconds=settings.clock_skew_tolerance_seconds,
            )

            if not is_fresh_result:
                if "stale" in rejection_reason:
                    result.stale_records += 1
                    age_hours = (reference_time - validated.posted_at).total_seconds() / 3600
                    logger.warning(
                        "freshness_rejected",
                        url=record.source_url,
                        source=record.source_name,
                        posted_at=validated.posted_at.isoformat(),
                        age_hours=round(age_hours, 1),
                        reason="stale_record",
                    )
                else:
                    result.future_dated_records += 1
                    delta_seconds = (validated.posted_at - reference_time).total_seconds()
                    logger.warning(
                        "future_date_rejected",
                        url=record.source_url,
                        source=record.source_name,
                        posted_at=validated.posted_at.isoformat(),
                        delta_seconds=round(delta_seconds, 0),
                        reason="future_dated",
                    )

                result.rejection_reasons.append(f"{record.source_url}: {rejection_reason}")
                continue

            raw_document_id = None
            if record.fetch_result is not None:
                try:
                    raw_doc = await raw_doc_repo.get_or_create(
                        source_name=record.source_name,
                        source_url=record.source_url,
                        canonical_url=record.fetch_result.url,
                        http_status=record.fetch_result.status_code,
                        content_hash=record.fetch_result.content_hash,
                        extraction_status="extracted",
                        publication_date_candidates={"posted_at": validated.posted_at.isoformat()},
                    )
                    raw_document_id = raw_doc.id
                except Exception as exc:
                    result.rejected += 1
                    logger.warning(
                        "raw_document_persistence_error",
                        url=record.source_url,
                        source=record.source_name,
                        error=str(exc),
                    )
                    await error_repo.record(
                        source_name=record.source_name,
                        url=record.source_url,
                        error_category="RawDocumentPersistenceError",
                        message=str(exc),
                        context={},
                    )
                    continue

            payload = validated.model_dump(exclude={"schema_version", "record_type"})
            payload["raw_document_id"] = raw_document_id
            payload["collected_at"] = reference_time

            # Resolve the employer onto a canonical entity. A failure here is
            # never fatal and never fabricates a link -- the job is persisted
            # with a null canonical_entity_id instead.
            try:
                resolution = await resolver.resolve(validated.company, source_url=record.source_url)
                payload["canonical_entity_id"] = resolution.canonical_entity_id
                result.entity_methods[resolution.method] = result.entity_methods.get(resolution.method, 0) + 1
                if resolution.resolved:
                    result.entities_resolved += 1
                    if resolution.created:
                        result.entities_created += 1
                else:
                    result.entities_unresolved += 1
            except Exception as exc:  # noqa: BLE001 - resolution must not break ingestion
                payload["canonical_entity_id"] = None
                result.entities_unresolved += 1
                logger.warning(
                    "entity_resolution_failed",
                    url=record.source_url,
                    source=record.source_name,
                    error=str(exc),
                )

            try:
                inserted = await job_repo.upsert(**payload)
                if inserted:
                    result.valid_records += 1
                    logger.debug(
                        "record_persisted",
                        url=record.source_url,
                        source=record.source_name,
                        title=validated.title[:50],
                    )
                else:
                    result.duplicates += 1
                    logger.debug(
                        "duplicate_skipped",
                        url=record.source_url,
                        source=record.source_name,
                    )
            except Exception as exc:
                result.rejected += 1
                result.rejection_reasons.append(f"{record.source_url}: {exc}")
                logger.warning(
                    "persistence_error",
                    url=record.source_url,
                    source=record.source_name,
                    error=str(exc),
                )
                await error_repo.record(
                    source_name=record.source_name,
                    url=record.source_url,
                    error_category="PersistenceError",
                    message=str(exc),
                    context={},
                )

    logger.info(
        "pipeline_complete",
        vertical="jobs",
        target=target,
        discovered=result.discovered,
        fetched=result.fetched,
        parsed=result.parsed,
        validated=result.valid_records + result.duplicates,
        rejected_stale=result.stale_records,
        rejected_future_dated=result.future_dated_records,
        rejected_invalid=result.invalid_records,
        persisted=result.valid_records,
        fetch_failed=result.fetch_failed,
        blocked=result.blocked,
        duplicate_skipped=result.duplicates,
        by_source=result.by_source,
        entities_resolved=result.entities_resolved,
        entities_created=result.entities_created,
        entities_unresolved=result.entities_unresolved,
        entity_methods=result.entity_methods,
    )

    return result
