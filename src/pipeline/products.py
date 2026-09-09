"""
Products vertical pipeline.

Wires together: the products adapters (discover/fetch/parse) -> worker pool
-> schema validation -> provenance persistence (RawDocument) -> entity
resolution -> idempotent persistence (ProductRepository).

Structurally identical to the startups/research/jobs pipelines and reuses the
same abstractions (`run_adapter`, `AsyncHttpClient`, the repositories, the
`EntityResolver`); nothing is duplicated here.

Sources (all official, public, unauthenticated REST APIs)
---------------------------------------------------------
1. ``huggingface_spaces``  -- deployed, publicly-listed AI applications
2. ``openrouter_models``   -- the commercial AI model catalogue (the only
   source that publishes real prices, so the only one that may populate
   ``pricing_model``)
3. ``huggingface_models``  -- published AI model products

They run in that fixed order, so a run of a given target is reproducible and
the cross-source dedup winner is deterministic.

Deduplication
-------------
Three layers, none of which is a name comparison:

* in-run, per source URL (cheap, avoids pointless writes)
* in-run, per ``dedup_key`` -- the source's own artifact identifier. When
  OpenRouter names the exact Hugging Face repo behind a model, both sources
  produce the same key and the artifact is stored once.
* the database unique constraints (``uq_products_source_url`` and
  ``uq_products_dedup_key``), which are the real guarantee.

Because identity is keyed on URLs/ids and never on names, two genuinely
different products with similar names always remain separate rows.

Anti-fabrication: the target is a ceiling, never a quota to fill. If the
sources cannot supply `target` real records, a `products_target_not_met`
warning is logged and the real count is reported -- no row is invented,
padded, or duplicated to close the gap.

Run via `python -m src.main --vertical products --target 1000`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger
from src.crawlers.http import AsyncHttpClient
from src.crawlers.huggingface_products import (
    HuggingFaceModelsAdapter,
    HuggingFaceSpacesAdapter,
)
from src.crawlers.openrouter_products import OpenRouterModelsAdapter
from src.pipeline.workers import run_adapter
from src.resolution import EntityResolver
from src.storage.repositories import (
    ProcessingErrorRepository,
    ProductRepository,
    RawDocumentRepository,
)
from src.validation.schemas import validate_product_record

logger = get_logger(component="products_pipeline")

# Fixed order == reproducible runs and a deterministic cross-source dedup
# winner. Adding a fourth source means appending its class here; the
# fill-forward allocation below already handles N adapters.
ADAPTER_CLASSES = [
    HuggingFaceSpacesAdapter,
    OpenRouterModelsAdapter,
    HuggingFaceModelsAdapter,
]

# A source's duplicate/short output is only knowable *after* it has been
# fetched, so one allocation pass cannot know how deep to read. Bounded
# re-passes let sources that still have unread records cover a shortfall
# caused by an earlier source's duplicates. The bound keeps a pathological
# source from looping forever; the loop also exits as soon as a full pass
# adds no new records.
MAX_ALLOCATION_PASSES = 4


@dataclass(slots=True)
class ProductsPipelineResult:
    target: int
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    valid_records: int = 0
    duplicates: int = 0
    cross_source_duplicates: int = 0
    rejected: int = 0
    entities_resolved: int = 0
    entities_created: int = 0
    entities_unresolved: int = 0
    entity_methods: dict[str, int] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)


async def run_products_pipeline(
    session: AsyncSession,
    *,
    target: int = 1000,
    max_concurrency: int = 20,
    page_size: int = 100,
) -> ProductsPipelineResult:
    result = ProductsPipelineResult(target=target)
    product_repo = ProductRepository(session)
    raw_doc_repo = RawDocumentRepository(session)
    error_repo = ProcessingErrorRepository(session)
    resolver = EntityResolver(session)

    async with AsyncHttpClient(max_concurrency=max_concurrency) as http_client:
        all_records: list = []
        seen_urls: set[str] = set()
        # dedup_key -> the source that first contributed it, so a re-read of
        # our own records is not miscounted as a cross-source collision.
        seen_keys: dict[str, str] = {}
        # adapter name -> how deep into its listing we have already asked it
        # to read, and whether it told us it has nothing left.
        requested_depth: dict[str, int] = {}
        exhausted: set[str] = set()

        # Fill-forward allocation, same shape as the startups/research
        # pipelines: each adapter is asked for a fair share of what is
        # *still* outstanding, so one source running dry does not silently
        # shrink the run -- the next source picks up the slack.
        #
        # Products needs one thing the other verticals do not: a source's
        # records can be dropped as duplicates of an *earlier* source's, and
        # that is only knowable after fetching. A single pass would let a
        # shortfall caused by the last source's duplicates stand, even when
        # that source still has unread records. So the passes repeat, each
        # time asking the surviving sources to read *deeper* (previous depth
        # + the new share) rather than re-requesting the same window. The
        # already-seen prefix is skipped by `seen_urls` and the unread tail
        # fills the gap. Order is fixed, so the result is deterministic.
        adapter_count = len(ADAPTER_CLASSES)
        for pass_index in range(MAX_ALLOCATION_PASSES):
            if len(all_records) >= target:
                break
            added_this_pass = 0

            for index, adapter_cls in enumerate(ADAPTER_CLASSES):
                remaining = target - len(all_records)
                if remaining <= 0:
                    break
                if adapter_cls.name in exhausted:
                    # The source already returned fewer records than asked
                    # for: it has no more. Re-reading it cannot invent any.
                    continue

                share = max(1, -(-remaining // (adapter_count - index)))  # ceil div
                previous_depth = requested_depth.get(adapter_cls.name, 0)
                depth = previous_depth + share
                adapter = adapter_cls(http_client, max_results=depth, page_size=page_size)
                stats, records = await run_adapter(adapter, max_concurrency=max_concurrency)
                requested_depth[adapter.name] = depth
                result.discovered += stats.discovered
                result.fetched += stats.fetched
                result.parsed += stats.parsed_records

                if len(records) < depth:
                    # The source itself said "that is all there is". Honoured,
                    # never padded.
                    exhausted.add(adapter.name)

                new_records = []
                for record in records:
                    if record.source_url in seen_urls:
                        # Either a re-read of a record we already hold, or the
                        # same URL twice. Identity is the URL, never the name.
                        continue
                    key = record.data.get("dedup_key")
                    if isinstance(key, str) and key in seen_keys:
                        # Same real-world artifact already contributed by an
                        # earlier source. Deterministic: source order is fixed.
                        if seen_keys[key] != adapter.name:
                            result.cross_source_duplicates += 1
                        continue
                    seen_urls.add(record.source_url)
                    if isinstance(key, str):
                        seen_keys[key] = adapter.name
                    new_records.append(record)

                # A record kept here is genuinely new: it survived both the
                # URL check and the source-derived dedup_key check, so the
                # count below is real records, never padding.
                result.by_source[adapter.name] = result.by_source.get(adapter.name, 0) + len(new_records)
                all_records.extend(new_records)
                added_this_pass += len(new_records)
                logger.info(
                    "products_adapter_collected",
                    source=adapter.name,
                    pass_index=pass_index,
                    asked_for=depth,
                    produced=len(records),
                    new=len(new_records),
                    running_total=len(all_records),
                )

            if added_this_pass == 0:
                # A whole pass added nothing new: every source is either
                # exhausted or only repeating itself. Stop instead of
                # spinning -- the shortfall is reported honestly below.
                break

        # The target is a ceiling, not a quota. `all_records` holds only
        # records a source actually returned, so it can be short but never
        # inflated.
        if len(all_records) < target:
            # Honest under-delivery. Nothing is invented to close the gap.
            logger.warning(
                "products_target_not_met",
                target=target,
                collected=len(all_records),
                by_source=dict(result.by_source),
            )

        for record in all_records:
            data = dict(record.data)
            data["source_name"] = record.source_name
            data["source_url"] = record.source_url

            validated, error = validate_product_record(data)
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

            # Resolve the *vendor* onto a canonical entity, reusing the same
            # resolver (and the same thresholds) as the other verticals.
            # Failure is never fatal and never fabricates a link -- the
            # product is persisted with a null canonical_entity_id instead.
            try:
                resolution = await resolver.resolve(
                    validated.startup_name, source_url=record.source_url
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
                inserted = await product_repo.upsert(**payload)
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
        "products_pipeline_complete",
        target=target,
        discovered=result.discovered,
        valid_records=result.valid_records,
        duplicates=result.duplicates,
        cross_source_duplicates=result.cross_source_duplicates,
        rejected=result.rejected,
        entities_resolved=result.entities_resolved,
        entities_created=result.entities_created,
        entities_unresolved=result.entities_unresolved,
        by_source=result.by_source,
    )
    return result
