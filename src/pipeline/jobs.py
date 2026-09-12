"""
Jobs vertical pipeline (Phase 7).

Orchestrates jobs ingestion from five sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from pydantic import HttpUrl
from sqlalchemy import select

from src.config.sources import Vertical, get_sources_for_vertical
from src.errors import BlockedSourceError
from src.extraction.urls import normalize_url
from src.storage.models import Job
from src.validation.job_dates import parse_job_timestamp

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.config.settings import get_settings


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
    reference_time = parse_job_timestamp(reference_time)
    if reference_time is None:
        raise ValueError("Jobs reference_time must be timezone-aware")

    settings = get_settings()
    result = JobsPipelineResult(target=target)
    if target <= 0:
        return result

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
        clock_skew_tolerance_seconds=0,
        freshness_window_hours=24,
    )

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        enabled = {source.name for source in get_sources_for_vertical(Vertical.JOBS)}
        adapters = [cls(http_client) for cls in ADAPTER_CLASSES if cls.name in enabled]
        result.by_source = {adapter.name: 0 for adapter in adapters}
        # max_items caps discovery URLs, not accepted records. Do not allocate
        # tiny quotas to a source before its stale records have been filtered.

        all_records = []
        for adapter in adapters:
            try:
                stats, records = await run_adapter(
                    adapter,
                    max_concurrency=max_concurrency,
                    max_items=target,
                )

                result.discovered += stats.discovered
                result.fetched += stats.fetched
                result.fetch_failed += stats.fetch_failed
                result.blocked += stats.blocked
                for message in stats.errors:
                    await error_repo.record(
                        source_name=adapter.name, url="N/A", error_category="SourceError",
                        message=message, context={"stage": "fetch_or_parse"},
                    )
                if stats.blocked:
                    await error_repo.record(
                        source_name=adapter.name, url="N/A", error_category="BlockedSourceError",
                        message=f"{stats.blocked} job fetches blocked; no substitute records",
                        context={},
                    )

                logger.info(
                    "adapter_complete",
                    source=adapter.name,
                    discovered=stats.discovered,
                    fetched=stats.fetched,
                    parsed_records=len(records),
                    fetch_failed=stats.fetch_failed,
                    blocked=stats.blocked,
                )

                all_records.extend(sorted(records, key=lambda record: (
                    record.source_url, str(record.data.get("posted_at")),
                    record.fetch_result.content_hash if record.fetch_result else "",
                )))
            except Exception as exc:
                if isinstance(exc, BlockedSourceError):
                    result.blocked += 1
                else:
                    result.fetch_failed += 1
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

        seen_urls = set()
        for record in all_records:
            if result.valid_records >= target:
                break
            data = dict(record.data)
            data["source_name"] = record.source_name
            result.parsed += 1
            data["url"] = normalize_url(data["url"]) if isinstance(data.get("url"), str) else data.get("url")
            metadata = dict(data.get("metadata_json") or {})
            metadata["source_url"] = normalize_url(record.source_url)
            metadata["collected_at"] = reference_time.isoformat()
            data["metadata_json"] = metadata

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
                window_hours=24,
                clock_skew_tolerance_seconds=0,
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

            fetched = record.fetch_result
            try:
                HttpUrl(record.source_url)
                if record.source_name not in enabled:
                    raise ValueError("Record from an unregistered or disabled Jobs source")
                if fetched is None or not 200 <= fetched.status_code < 300 or not fetched.text:
                    raise ValueError("Missing successful raw source response")
                HttpUrl(fetched.url)
                raw_record = metadata.get("raw_record")
                field_name = metadata.get("date_field")
                allowed_date_fields = {
                    "remoteok_ai_jobs": "date", "workingnomads_ai_jobs": "pub_date",
                    "ycombinator_hn_whoishiring": "created_at",
                    "wellfound_ai_jobs": "datePosted", "builtin_ai_jobs": "datePosted",
                }
                if not isinstance(raw_record, dict) or field_name != allowed_date_fields.get(record.source_name):
                    raise ValueError("Missing source posting-date evidence")
                if (raw_record.get(field_name) != metadata.get("date_value")
                        or parse_job_timestamp(metadata.get("date_value")) != validated.posted_at):
                    raise ValueError("Posting timestamp does not match raw source evidence")
            except (ValueError, TypeError) as exc:
                result.invalid_records += 1
                result.rejection_reasons.append(f"{record.source_url}: {exc}")
                await error_repo.record(
                    source_name=record.source_name, url=record.source_url,
                    error_category="provenance_error", message=str(exc), context={},
                )
                continue

            source_url = metadata["source_url"]
            # Global URL dedup within and across runs, in configured source order.
            existing = await session.execute(select(Job.id).where(
                (Job.url == validated.url) | (Job.metadata_json["source_url"].as_string() == source_url)
            ).limit(1))
            if validated.url in seen_urls or source_url in seen_urls or existing.first() is not None:
                result.duplicates += 1
                continue

            try:
                content_hash = hashlib.sha256(fetched.text.encode("utf-8")).hexdigest()
                raw_path = Path(settings.raw_storage_local_path).resolve() / "jobs" / f"{content_hash}.txt"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                if not raw_path.exists():
                    raw_path.write_text(fetched.text, encoding="utf-8")
                # A feed response may contain many jobs. RawDocument describes the
                # fetched feed; each Job preserves its own URL/raw item/date evidence.
                raw_doc = await raw_doc_repo.get_or_create(
                    source_name=record.source_name,
                    source_url=fetched.url,
                    canonical_url=fetched.url,
                    http_status=fetched.status_code,
                    content_hash=content_hash,
                    raw_content_location=str(raw_path),
                    extraction_status="extracted",
                )
                raw_document_id = raw_doc.id
                metadata["fetch_url"] = fetched.url
                metadata["content_hash"] = content_hash
            except Exception as exc:
                result.rejected += 1
                await session.rollback()
                await error_repo.record(
                    source_name=record.source_name, url=record.source_url,
                    error_category="RawDocumentPersistenceError", message=str(exc), context={},
                )
                continue

            payload = validated.model_dump(exclude={"schema_version", "record_type"})
            payload["metadata_json"] = metadata
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
                    result.by_source[record.source_name] += 1
                    seen_urls.update((validated.url, source_url))
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
                await session.rollback()
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
