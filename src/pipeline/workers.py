"""
Worker pool: runs discover() -> fetch() -> parse() for one adapter with
bounded concurrency, in-run URL dedup, structured logging, and graceful
shutdown on cancellation.

In-run dedup here is a fast-path optimization only -- the real dedup
guarantee comes from the database unique constraints (Section 28), so this
remains safe even across multiple processes that don't share this set.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.errors import BlockedSourceError, PayloadTooLargeError, PipelineError
from src.extraction.urls import normalize_url

logger = get_logger(component="worker_pool")


@dataclass(slots=True)
class RunStats:
    discovered: int = 0
    fetched: int = 0
    fetch_failed: int = 0
    parsed_records: int = 0
    skipped_duplicate: int = 0
    blocked: int = 0
    errors: list[str] = field(default_factory=list)


async def run_adapter(
    adapter: SourceAdapter,
    *,
    max_concurrency: int,
    max_items: int | None = None,
) -> tuple[RunStats, list[ParsedRecord]]:
    stats = RunStats()
    records: list[ParsedRecord] = []
    seen_urls: set[str] = set()

    semaphore = asyncio.Semaphore(max_concurrency)
    tasks: set[asyncio.Task] = set()

    async def handle_one(discovered: DiscoveredUrl) -> None:
        async with semaphore:
            try:
                fetch_result = await adapter.fetch(discovered)
                stats.fetched += 1
            except BlockedSourceError as exc:
                stats.blocked += 1
                logger.warning("source_blocked", source=adapter.name, url=discovered.url, error=str(exc))
                return
            except PayloadTooLargeError as exc:
                # Fetch-time 413 (rare, e.g. an API rejecting a large page
                # size param) -- log and skip; content-time 413 is handled
                # by the chunker downstream, not here.
                stats.fetch_failed += 1
                stats.errors.append(str(exc))
                logger.warning("fetch_413", source=adapter.name, url=discovered.url)
                return
            except PipelineError as exc:
                stats.fetch_failed += 1
                stats.errors.append(str(exc))
                logger.warning(
                    "fetch_failed", source=adapter.name, url=discovered.url, error=str(exc), error_type=type(exc).__name__
                )
                return

            try:
                parsed = await adapter.parse(fetch_result, discovered)
            except PipelineError as exc:
                stats.fetch_failed += 1
                stats.errors.append(str(exc))
                logger.warning("parse_failed", source=adapter.name, url=discovered.url, error=str(exc))
                return

            stats.parsed_records += len(parsed)
            records.extend(parsed)

    try:
        async for discovered in adapter.discover():
            stats.discovered += 1
            norm = normalize_url(discovered.url)
            if norm in seen_urls:
                stats.skipped_duplicate += 1
                continue
            seen_urls.add(norm)

            task = asyncio.create_task(handle_one(discovered))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

            if max_items is not None and stats.discovered >= max_items:
                break

        if tasks:
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.warning("worker_pool_cancelled", source=adapter.name, pending_tasks=len(tasks))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        logger.info(
            "adapter_run_complete",
            source=adapter.name,
            discovered=stats.discovered,
            fetched=stats.fetched,
            fetch_failed=stats.fetch_failed,
            parsed_records=stats.parsed_records,
            skipped_duplicate=stats.skipped_duplicate,
            blocked=stats.blocked,
        )

    return stats, records
