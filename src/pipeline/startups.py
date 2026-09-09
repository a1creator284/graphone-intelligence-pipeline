"""
Startups vertical pipeline.

Wires together: YCombinatorStartupsAdapter (discover/fetch/parse) -> worker
pool -> schema validation -> provenance persistence (RawDocument) -> entity
resolution -> idempotent persistence (StartupRepository).

Structurally identical to the research/jobs pipelines and reuses the same
abstractions (`run_adapter`, `AsyncHttpClient`, the repositories, the
`EntityResolver`); nothing is duplicated here.

Anti-fabrication: the target is a ceiling, never a quota to fill. If the
sources cannot supply `target` real records, a `startups_target_not_met`
warning is logged and the real count is reported -- no row is invented,
padded, or duplicated to close the gap.

Run via `python -m src.main --vertical startups`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.config.settings import get_settings
from src.crawlers.http import AsyncHttpClient
from src.crawlers.ycombinator_startups import (
    DEFAULT_ALGOLIA_API_KEY,
    DEFAULT_ALGOLIA_APP_ID,
    YCombinatorStartupsAdapter,
)
from src.pipeline.workers import run_adapter
from src.resolution import EntityResolver
from src.storage.repositories import (
    ProcessingErrorRepository,
    RawDocumentRepository,
    StartupRepository,
)
from src.validation.schemas import validate_startup_record

logger = get_logger(component="startups_pipeline")

# Only the YC directory is wired today. Adding a second startups source means
# appending its class here -- the fill-forward allocation below already
# handles N adapters.
ADAPTER_CLASSES = [YCombinatorStartupsAdapter]


@dataclass(slots=True)
class StartupsPipelineResult:
    target: int
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    valid_records: int = 0
    duplicates: int = 0
    rejected: int = 0
    entities_resolved: int = 0
    entities_created: int = 0
    entities_unresolved: int = 0
    entity_methods: dict[str, int] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)


async def run_startups_pipeline(
    session: AsyncSession,
    *,
    target: int = 1000,
    max_concurrency: int = 20,
    page_size: int = 100,
) -> StartupsPipelineResult:
    result = StartupsPipelineResult(target=target)
    startup_repo = StartupRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)
    resolver = EntityResolver(session)

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        settings = get_settings()

        def build_adapter(adapter_cls, max_results: int):
            if adapter_cls is YCombinatorStartupsAdapter:
                return YCombinatorStartupsAdapter(
                    http_client,
                    app_id=settings.yc_algolia_app_id or DEFAULT_ALGOLIA_APP_ID,
                    api_key=settings.yc_algolia_api_key or DEFAULT_ALGOLIA_API_KEY,
                    max_results=max_results,
                    page_size=page_size,
                )
            return adapter_cls(  # pragma: no cover - no second adapter yet
                http_client, max_results=max_results, page_size=page_size
            )

        all_records: list = []
        seen_urls: set[str] = set()

        # Fill-forward allocation, same shape as the research pipeline: each
        # adapter is asked for a fair share of what is *still* outstanding, so
        # one source running dry does not silently shrink the run.
        adapter_count = len(ADAPTER_CLASSES)
        for index, adapter_cls in enumerate(ADAPTER_CLASSES):
            remaining = target - len(all_records)
            if remaining <= 0:
                break
            share = max(1, -(-remaining // (adapter_count - index)))  # ceil div
            adapter = build_adapter(adapter_cls, share)
            stats, records = await run_adapter(adapter, max_concurrency=max_concurrency)
            result.discovered += stats.discovered
            result.fetched += stats.fetched
            result.parsed += stats.parsed_records

            # Deterministic cross-page/cross-source dedup on the canonical
            # source URL. The DB unique constraint is the real guarantee;
            # this just avoids pointless writes.
            new_records = [r for r in records if r.source_url not in seen_urls]
            seen_urls.update(r.source_url for r in new_records)
            result.by_source[adapter.name] = result.by_source.get(adapter.name, 0) + len(new_records)
            all_records.extend(new_records)
            logger.info(
                "startups_adapter_collected",
                source=adapter.name,
                asked_for=share,
                produced=len(records),
                new=len(new_records),
                running_total=len(all_records),
            )

        if len(all_records) < target:
            # Honest under-delivery. Nothing is invented to close the gap.
            logger.warning(
                "startups_target_not_met",
                target=target,
                collected=len(all_records),
                by_source=dict(result.by_source),
            )

        for record in all_records:
            data = dict(record.data)
            data["source_name"] = record.source_name
            data["source_url"] = record.source_url

            validated, error = validate_startup_record(data)
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

            # Provenance: persist the raw fetch before the structured record.
            raw_document_id = None
            if record.fetch_result is not None:
                try:
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
                except Exception as exc:  # noqa: BLE001 - never fatal
                    logger.warning(
                        "raw_document_persist_failed", url=record.source_url, error=str(exc)
                    )
            payload["raw_document_id"] = raw_document_id

            # Resolve the company onto a canonical entity. Failure is never
            # fatal and never fabricates a link -- the startup is persisted
            # with a null canonical_entity_id instead.
            try:
                resolution = await resolver.resolve(
                    validated.entity_name, source_url=record.source_url
                )
                payload["canonical_entity_id"] = resolution.canonical_entity_id
                result.entity_methods[resolution.method] = (
                    result.entity_methods.get(resolution.method, 0) + 1
                )
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
                inserted = await startup_repo.upsert(**payload)
                if inserted:
                    result.valid_records += 1
                else:
                    result.duplicates += 1
            except Exception as exc:  # noqa: BLE001 - record and continue
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
        "startups_pipeline_complete",
        target=target,
        discovered=result.discovered,
        valid_records=result.valid_records,
        duplicates=result.duplicates,
        rejected=result.rejected,
        entities_resolved=result.entities_resolved,
        entities_created=result.entities_created,
        entities_unresolved=result.entities_unresolved,
        by_source=result.by_source,
    )
    return result
