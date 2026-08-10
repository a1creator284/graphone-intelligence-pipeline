"""
Research paper vertical pipeline (Phase 4).

Wires together: ArxivAdapter + PapersWithCodeAdapter (discover/fetch/parse)
-> worker pool -> schema validation -> GitHub enrichment (only for papers
whose source explicitly linked a repo) -> provenance persistence
(RawDocument) -> idempotent persistence (ResearchPaperRepository).

Run via `scripts/run_research.py` or `python -m src.main --vertical research`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.crawlers.arxiv import ArxivAdapter
from src.crawlers.http import AsyncHttpClient
from src.crawlers.papers_with_code import PapersWithCodeAdapter
from src.errors import RateLimitError
from src.extraction.github import GitHubEnrichmentClient
from src.pipeline.workers import run_adapter
from src.storage.repositories import ProcessingErrorRepository, RawDocumentRepository, ResearchPaperRepository
from src.validation.schemas import validate_research_paper

logger = get_logger(component="research_pipeline")

# Only these adapters are wired for the research vertical (Section 10
# priority: arXiv first, then Papers With Code).
ADAPTER_CLASSES = [ArxivAdapter, PapersWithCodeAdapter]


@dataclass(slots=True)
class ResearchPipelineResult:
    target: int
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    valid_records: int = 0
    duplicates: int = 0
    rejected: int = 0
    github_enriched: int = 0
    github_enrichment_skipped_rate_limited: bool = False
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)


async def run_research_pipeline(
    session: AsyncSession,
    *,
    target: int = 1000,
    max_concurrency: int = 20,
    search_query: str = "cat:cs.AI",
) -> ResearchPipelineResult:
    result = ResearchPipelineResult(target=target)
    paper_repo = ResearchPaperRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        github_client = GitHubEnrichmentClient(http_client)

        # Split the overall target roughly evenly across the two adapters so
        # neither source is starved when target is small.
        per_adapter_target = max(1, target // len(ADAPTER_CLASSES))

        adapters = [
            ArxivAdapter(http_client, search_query=search_query, max_results=per_adapter_target, page_size=50),
            PapersWithCodeAdapter(http_client, max_results=per_adapter_target, page_size=50),
        ]

        all_records = []
        for adapter in adapters:
            stats, records = await run_adapter(adapter, max_concurrency=max_concurrency)
            result.discovered += stats.discovered
            result.fetched += stats.fetched
            result.parsed += stats.parsed_records
            result.by_source[adapter.name] = len(records)
            all_records.extend(records)

        for record in all_records:
            data = dict(record.data)
            data["source_name"] = record.source_name

            validated, error = validate_research_paper(data)
            if validated is None:
                result.rejected += 1
                result.rejection_reasons.append(f"{record.source_url}: {error}")
                await error_repo.record(
                    source_name=record.source_name,
                    url=record.source_url,
                    error_category="ValidationError",
                    message=error or "unknown validation failure",
                    context={"record_type": record.record_type},
                )
                continue

            payload = validated.model_dump(exclude={"schema_version", "record_type"})

            # Provenance: persist the raw fetch, when we have one, before the
            # structured record (Section 8).
            raw_document_id = None
            if record.fetch_result is not None:
                raw_doc = await raw_doc_repo.get_or_create(
                    source_name=record.source_name,
                    source_url=record.source_url,
                    canonical_url=record.fetch_result.url,
                    http_status=record.fetch_result.status_code,
                    content_hash=record.fetch_result.content_hash,
                    extraction_status="parsed",
                    publication_date_candidates={},
                )
                raw_document_id = raw_doc.id
            payload["raw_document_id"] = raw_document_id

            # GitHub enrichment -- only when the adapter itself discovered an
            # explicit repo link; never inferred here (Section 10).
            if payload.get("github_url") and not result.github_enrichment_skipped_rate_limited:
                try:
                    meta = await github_client.get_repo_metadata(payload["github_url"])
                    if meta is not None:
                        payload["github_stars"] = meta.stargazers_count
                        result.github_enriched += 1
                    else:
                        # Repo not found / unverifiable via the API -- null it
                        # out rather than keep an unconfirmed link.
                        payload["github_url"] = None
                        payload["github_stars"] = None
                except RateLimitError:
                    logger.warning(
                        "github_enrichment_rate_limited_disabling_for_run",
                        note="Remaining papers keep github_url as discovered but leave "
                        "github_stars null rather than fabricate a count.",
                    )
                    result.github_enrichment_skipped_rate_limited = True
                    payload["github_stars"] = None

            try:
                inserted = await paper_repo.upsert(**payload)
                if inserted:
                    result.valid_records += 1
                else:
                    result.duplicates += 1
            except Exception as exc:  # noqa: BLE001 - record and continue, don't crash the run
                result.rejected += 1
                result.rejection_reasons.append(f"{record.source_url}: {exc}")
                await error_repo.record(
                    source_name=record.source_name,
                    url=record.source_url,
                    error_category="PersistenceError",
                    message=str(exc),
                    context={},
                )

    logger.info(
        "research_pipeline_complete",
        target=target,
        discovered=result.discovered,
        valid_records=result.valid_records,
        duplicates=result.duplicates,
        rejected=result.rejected,
        github_enriched=result.github_enriched,
        by_source=result.by_source,
    )
    return result
