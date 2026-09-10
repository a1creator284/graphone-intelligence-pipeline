"""
News vertical pipeline (Phase 6).

Orchestrates news ingestion from five AI news sources (HackerNews, TechCrunch,
The Verge, MIT Tech Review, The Decoder) following the existing research
pipeline architecture.

Workflow:
1. Wire five news source adapters
2. Run each adapter via worker pool (discover -> fetch -> parse)
3. Validate each ParsedRecord (schema validation)
4. Check freshness (strict 24-hour window; no future timestamps)
5. Persist raw HTML (provenance) and validated records (deduplication)

Run via `python -m src.main --vertical news`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.config.sources import Vertical, get_sources_for_vertical
from src.crawlers.hackernews import HackerNewsAIAdapter
from src.crawlers.http import AsyncHttpClient
from src.crawlers.mitreview import MITTechReviewAIAdapter
from src.crawlers.thedecoder import TheDecoderAdapter
from src.errors import BlockedSourceError, PipelineError
from src.crawlers.techcrunch import TechCrunchAIAdapter
from src.crawlers.theverge import TheVergeAIAdapter
from src.pipeline.workers import run_adapter
from src.storage.repositories import NewsRepository, ProcessingErrorRepository, RawDocumentRepository
from src.validation.freshness import is_fresh
from src.validation.schemas import validate_news_record

logger = get_logger(component="news_pipeline")

# Five news source adapters wired for the news vertical (Task 9.1)
ADAPTER_CLASSES = [
    HackerNewsAIAdapter,
    TechCrunchAIAdapter,
    TheVergeAIAdapter,
    MITTechReviewAIAdapter,
    TheDecoderAdapter,
]


@dataclass(slots=True)
class NewsPipelineResult:
    """Pipeline execution result matching research pipeline pattern."""

    target: int
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    valid_records: int = 0
    duplicates: int = 0
    rejected: int = 0
    extraction_failed: int = 0
    stale_records: int = 0
    future_dated_records: int = 0
    invalid_records: int = 0
    fetch_failed: int = 0
    blocked: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)
    persisted_by_source: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, list[str]] = field(default_factory=dict)
    discovery_duplicates: int = 0


async def run_news_pipeline(
    session: AsyncSession,
    *,
    target: int = 1000,
    max_concurrency: int = 20,
    reference_time: datetime | None = None,
) -> NewsPipelineResult:
    """
    Run the news ingestion pipeline.

    Args:
        session: Database session for persistence
        target: Target number of articles to discover (split across adapters)
        max_concurrency: Maximum concurrent HTTP requests
        reference_time: Reference time for freshness validation (defaults to now)

    Returns:
        NewsPipelineResult with statistics from the pipeline run

    Pipeline Flow (Task 9):
    1. Wire all 5 news source adapters
    2. Run each adapter via worker pool (discover -> fetch -> parse)
    3. Validate each ParsedRecord (schema validation)
    4. Check freshness (strict 24-hour window; no future timestamps)
    5. Persist raw HTML (provenance) and validated records (deduplication)
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    if reference_time.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    reference_time = reference_time.astimezone(timezone.utc)
    if target < 1 or max_concurrency < 1:
        raise ValueError("target and max_concurrency must be positive")
    enabled = {s.name for s in get_sources_for_vertical(Vertical.NEWS)}
    adapter_classes = [cls for cls in ADAPTER_CLASSES if cls.name in enabled]
    result = NewsPipelineResult(target=target)

    # Repositories for persistence (following research pipeline pattern)
    news_repo = NewsRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)

    # Task 9.3: Structured logging - pipeline start
    logger.info(
        "pipeline_start",
        vertical="news",
        reference_time=reference_time.isoformat(),
        target=target,
        max_concurrency=max_concurrency,
        clock_skew_tolerance_seconds=0,
        freshness_window_hours=24,
    )

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        # Task 9.1: Wire news source adapters
        # Split target evenly across adapters (following research pipeline pattern)
        if not adapter_classes:
            return result
        per_adapter_target = max(1, target // len(adapter_classes))
        adapters = [cls(http_client, reference_time=reference_time) for cls in adapter_classes]

        # Run each adapter and collect all records
        all_records = []
        for adapter in adapters:
            result.by_source[adapter.name] = 0
            result.persisted_by_source[adapter.name] = 0
            try:
                stats, records = await run_adapter(
                    adapter,
                    max_concurrency=max_concurrency,
                    max_items=per_adapter_target,
                )
            except PipelineError as exc:
                # Discovery happens before article tasks in these single-response adapters.
                # A broken feed/API must not abort the other News sources.
                result.source_errors[adapter.name] = [str(exc)]
                if isinstance(exc, BlockedSourceError):
                    result.blocked += 1
                else:
                    result.fetch_failed += 1
                logger.warning("news_source_failed", source=adapter.name, error=str(exc))
                await error_repo.record(
                    source_name=adapter.name, url=getattr(adapter, "feed_url", getattr(adapter, "BASE_URL", None)),
                    error_category=type(exc).__name__, message=str(exc), context={"stage": "discovery"},
                )
                continue

            # Aggregate stats from worker pool
            result.discovered += stats.discovered
            result.fetched += stats.fetched
            result.parsed += len(records)
            result.extraction_failed += getattr(adapter, "extraction_failed", 0)
            result.discovery_duplicates += stats.skipped_duplicate
            if stats.errors:
                result.source_errors[adapter.name] = stats.errors
            result.fetch_failed += stats.fetch_failed
            result.blocked += stats.blocked
            result.by_source[adapter.name] = len(records)

            # Task 9.3: Structured logging - adapter complete
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

        # Task 9.2: Implement validation and persistence flow
        for record in all_records:
            # Extract metadata from ParsedRecord
            data = dict(record.data)
            data["source_name"] = record.source_name
            data["url"] = record.source_url

            # Check if extraction failed (anti-hallucination: no full_text → reject)
            if data.get("extracted_metadata", {}).get("extraction_failed"):
                result.extraction_failed += 1
                # Task 9.3: Log extraction failure
                logger.warning(
                    "extraction_failed",
                    url=record.source_url,
                    source=record.source_name,
                    bytes_fetched=len(record.fetch_result.text) if record.fetch_result else 0,
                    extraction_library="trafilatura+newspaper3k",
                    reason="no_content_or_too_short",
                )
                continue

            # Gate 1: Schema validation
            validated, error = validate_news_record(data)
            if validated is None:
                result.invalid_records += 1
                result.rejection_reasons.append(f"{record.source_url}: {error}")

                # Task 9.3: Log validation failure
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

            # Gate 2: Freshness check
            is_fresh_result, rejection_reason = is_fresh(
                validated.published_at,
                reference_time,
                window_hours=24,
                clock_skew_tolerance_seconds=0,
            )

            if not is_fresh_result:
                # Classify rejection type (stale vs future_dated)
                if "stale" in rejection_reason:
                    result.stale_records += 1
                    # Task 9.3: Log freshness rejection (stale)
                    age_hours = (reference_time - validated.published_at).total_seconds() / 3600
                    logger.warning(
                        "freshness_rejected",
                        url=record.source_url,
                        source=record.source_name,
                        published_at=validated.published_at.isoformat(),
                        age_hours=round(age_hours, 1),
                        freshness_window_hours=24,
                        reason="stale_record",
                    )
                else:
                    result.future_dated_records += 1
                    # Task 9.3: Log future date rejection
                    delta_seconds = (validated.published_at - reference_time).total_seconds()
                    logger.warning(
                        "future_date_rejected",
                        url=record.source_url,
                        source=record.source_name,
                        published_at=validated.published_at.isoformat(),
                        reference_time=reference_time.isoformat(),
                        delta_seconds=round(delta_seconds, 0),
                        tolerance_seconds=0,
                        reason="future_dated",
                    )

                result.rejection_reasons.append(f"{record.source_url}: {rejection_reason}")
                continue

            # Provenance: persist raw HTML before structured record
            raw_document_id = None
            if record.fetch_result is not None:
                raw_doc = await raw_doc_repo.get_or_create(
                    source_name=record.source_name,
                    source_url=record.source_url,
                    canonical_url=record.fetch_result.url,
                    http_status=record.fetch_result.status_code,
                    content_hash=record.fetch_result.content_hash,
                    extraction_status="extracted",
                    publication_date_candidates=data.get("publication_date_candidates", {}),
                )
                raw_document_id = raw_doc.id

            # Prepare payload for persistence
            payload = validated.model_dump(exclude={"schema_version", "record_type"})
            payload["extracted_metadata"] = {
                **payload["extracted_metadata"],
                "source_url": record.source_url,
                "source_name": record.source_name,
                "publication_date_candidates": data.get("publication_date_candidates", {}),
            }
            payload["raw_document_id"] = raw_document_id
            payload["collected_at"] = reference_time

            # Gate 3: Persistence with deduplication
            try:
                inserted = await news_repo.upsert(**payload)
                if inserted:
                    result.valid_records += 1
                    result.persisted_by_source[record.source_name] = result.persisted_by_source.get(record.source_name, 0) + 1
                    # Task 9.3: Log successful persistence (optional, can be noisy)
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
            except Exception as exc:  # noqa: BLE001 - record and continue
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

    # Task 9.3: Structured logging - pipeline complete
    logger.info(
        "pipeline_complete",
        vertical="news",
        target=target,
        discovered=result.discovered,
        fetched=result.fetched,
        full_text_extracted=result.valid_records + result.duplicates,
        extraction_failed=result.extraction_failed,
        validated=result.valid_records + result.duplicates,
        rejected_stale=result.stale_records,
        rejected_future_dated=result.future_dated_records,
        rejected_invalid=result.invalid_records,
        persisted=result.valid_records,
        fetch_failed=result.fetch_failed,
        blocked=result.blocked,
        duplicate_skipped=result.duplicates,
        by_source=result.by_source,
        persisted_by_source=result.persisted_by_source,
        source_errors=result.source_errors,
        discovery_duplicates=result.discovery_duplicates,
    )

    return result
