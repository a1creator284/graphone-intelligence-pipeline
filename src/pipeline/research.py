"""
Research paper vertical pipeline (Phase 4).

Wires together: ArxivAdapter + OpenAlexAdapter (discover/fetch/parse)
-> worker pool -> schema validation -> GitHub enrichment (only for papers
whose source explicitly linked a repo) -> provenance persistence
(RawDocument) -> idempotent persistence (ResearchPaperRepository).

OpenAlex replaced Papers With Code, whose API is dead upstream (302 -> HTML).
The PWC adapter module and its registry entry are retained but disabled.

Run via `scripts/run_research.py` or `python -m src.main --vertical research`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.config.settings import get_settings
from src.crawlers.arxiv import ArxivAdapter
from src.crawlers.http import AsyncHttpClient
from src.crawlers.openalex import OpenAlexAdapter
from src.errors import RateLimitError
from src.extraction.github import GitHubEnrichmentClient
from src.pipeline.workers import run_adapter
from src.storage.repositories import ProcessingErrorRepository, RawDocumentRepository, ResearchPaperRepository
from src.validation.schemas import validate_research_paper

logger = get_logger(component="research_pipeline")

# Only these adapters are wired for the research vertical (Section 10
# priority: arXiv first, then OpenAlex). Papers With Code is dead upstream
# and is disabled in the source registry rather than listed here.
ADAPTER_CLASSES = [ArxivAdapter, OpenAlexAdapter]


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

        def build_adapter(adapter_cls, max_results: int):
            if adapter_cls is ArxivAdapter:
                return ArxivAdapter(
                    http_client, search_query=search_query, max_results=max_results, page_size=50
                )
            if adapter_cls is OpenAlexAdapter:
                return OpenAlexAdapter(
                    http_client,
                    max_results=max_results,
                    page_size=50,
                    mailto=get_settings().openalex_mailto,
                )
            return adapter_cls(http_client, max_results=max_results, page_size=50)  # pragma: no cover

        all_records: list = []
        seen_urls: set[str] = set()
        # requested/exhausted are keyed by adapter class so a second pass can
        # tell "this source ran dry" from "this source had more to give".
        requested: dict[type, int] = {}
        exhausted: dict[type, bool] = {}

        async def collect(adapter, asked_for: int) -> int:
            """Run one adapter and keep only records not already collected.
            Returns how many records the adapter produced (before dedup)."""
            stats, records = await run_adapter(adapter, max_concurrency=max_concurrency)
            result.discovered += stats.discovered
            result.fetched += stats.fetched
            result.parsed += stats.parsed_records
            new_records = [r for r in records if r.source_url not in seen_urls]
            seen_urls.update(r.source_url for r in new_records)
            result.by_source[adapter.name] = result.by_source.get(adapter.name, 0) + len(new_records)
            all_records.extend(new_records)
            logger.info(
                "research_adapter_collected",
                source=adapter.name,
                asked_for=asked_for,
                produced=len(records),
                new=len(new_records),
                running_total=len(all_records),
            )
            return len(records)

        # Pass 1: fill-forward allocation. Instead of a fixed
        # `target // len(ADAPTER_CLASSES)` split -- which silently misses the
        # target whenever one source runs dry -- each adapter is asked for a
        # fair share of what is *still outstanding*, so a shortfall from an
        # earlier source is carried forward to later ones.
        adapter_count = len(ADAPTER_CLASSES)
        for index, adapter_cls in enumerate(ADAPTER_CLASSES):
            remaining = target - len(all_records)
            if remaining <= 0:
                break
            share = max(1, -(-remaining // (adapter_count - index)))  # ceil div
            produced = await collect(build_adapter(adapter_cls, share), share)
            requested[adapter_cls] = share
            exhausted[adapter_cls] = produced < share

        # Pass 2: if the last adapter(s) came up short, give the sources that
        # still had records to give one chance to make up the difference.
        # Bounded to a single extra round, and records already collected are
        # dropped by URL -- the target is never padded with fabricated rows.
        if target - len(all_records) > 0:
            for adapter_cls in ADAPTER_CLASSES:
                shortfall = target - len(all_records)
                if shortfall <= 0:
                    break
                if exhausted.get(adapter_cls, True):
                    continue  # source ran dry; asking again only re-fetches
                topped_up_to = requested[adapter_cls] + shortfall
                await collect(build_adapter(adapter_cls, topped_up_to), topped_up_to)

        if len(all_records) < target:
            # Honest under-delivery: the sources simply did not have enough
            # matching records. Nothing is invented to close the gap.
            logger.warning(
                "research_target_not_met",
                target=target,
                collected=len(all_records),
                by_source=dict(result.by_source),
            )

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
