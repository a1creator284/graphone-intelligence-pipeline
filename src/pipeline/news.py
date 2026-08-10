"""
News vertical pipeline (Phase 6).

Establishes the foundation for AI news ingestion following the existing
research pipeline architecture. Full adapter/extraction/validation
implementation will be added in subsequent tasks.

Run via `python -m src.main --vertical news`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.crawlers.http import AsyncHttpClient
from src.storage.repositories import NewsRepository, ProcessingErrorRepository, RawDocumentRepository

logger = get_logger(component="news_pipeline")


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
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)


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

    Note:
        This is the foundation implementation for Task 1.1.
        Source adapters, article extraction, and full validation will be
        added in subsequent tasks (Tasks 2-10).
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    result = NewsPipelineResult(target=target)

    # Repositories for persistence (following research pipeline pattern)
    news_repo = NewsRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        # TODO (Task 5-7): Wire news source adapters here
        # - HackerNewsAIAdapter
        # - TechCrunchAIAdapter
        # - TheVergeAIAdapter
        # - MITTechReviewAIAdapter
        # - SyncedReviewAdapter
        #
        # TODO (Task 2): Implement ArticleExtractor for full-text extraction
        # TODO (Task 3): Implement freshness validation with clock skew tolerance
        # TODO (Task 9): Implement validation and persistence flow
        #
        # For now, this foundation establishes the pipeline structure without
        # implementing news-specific crawling logic.

        logger.info(
            "news_pipeline_foundation",
            note="News adapters and extraction will be wired in subsequent tasks",
            target=target,
            max_concurrency=max_concurrency,
            reference_time=reference_time.isoformat(),
        )

    logger.info(
        "news_pipeline_complete",
        target=target,
        discovered=result.discovered,
        valid_records=result.valid_records,
        duplicates=result.duplicates,
        rejected=result.rejected,
        extraction_failed=result.extraction_failed,
        by_source=result.by_source,
    )
    return result
