from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import BlockedSourceError, NetworkError
from src.pipeline.workers import run_adapter


class FakeAdapter(SourceAdapter):
    """No real HTTP -- discover/fetch/parse are fully in-memory so the test
    suite never depends on a live site (Section 38)."""

    name = "fake_source"
    vertical = "news"

    def __init__(self, urls: list[str], fail_urls: set[str] | None = None, blocked_urls: set[str] | None = None):
        self.urls = urls
        self.fail_urls = fail_urls or set()
        self.blocked_urls = blocked_urls or set()

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        for u in self.urls:
            yield DiscoveredUrl(url=u)

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        if discovered.url in self.blocked_urls:
            raise BlockedSourceError("blocked", context={"url": discovered.url})
        if discovered.url in self.fail_urls:
            raise NetworkError("boom", context={"url": discovered.url})
        return FetchResult(url=discovered.url, status_code=200, text="<html>ok</html>", content_hash="deadbeef", headers={})

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        return [ParsedRecord(record_type="news", data={"title": "x"}, source_name=self.name, source_url=fetch_result.url)]


@pytest.mark.asyncio
async def test_all_urls_processed_successfully():
    adapter = FakeAdapter(urls=[f"https://example.com/{i}" for i in range(10)])
    stats, records = await run_adapter(adapter, max_concurrency=4)
    assert stats.discovered == 10
    assert stats.fetched == 10
    assert stats.parsed_records == 10
    assert len(records) == 10


@pytest.mark.asyncio
async def test_duplicate_discovered_urls_are_skipped():
    adapter = FakeAdapter(urls=["https://example.com/a", "https://example.com/a/", "https://example.com/b"])
    stats, records = await run_adapter(adapter, max_concurrency=4)
    assert stats.discovered == 3
    assert stats.skipped_duplicate == 1  # trailing-slash duplicate of /a
    assert len(records) == 2


@pytest.mark.asyncio
async def test_failed_fetches_dont_crash_the_run():
    adapter = FakeAdapter(
        urls=["https://example.com/good", "https://example.com/bad"],
        fail_urls={"https://example.com/bad"},
    )
    stats, records = await run_adapter(adapter, max_concurrency=4)
    assert stats.fetched == 1
    assert stats.fetch_failed == 1
    assert len(records) == 1


@pytest.mark.asyncio
async def test_blocked_source_is_recorded_not_bypassed():
    adapter = FakeAdapter(
        urls=["https://example.com/ok", "https://example.com/blocked"],
        blocked_urls={"https://example.com/blocked"},
    )
    stats, records = await run_adapter(adapter, max_concurrency=4)
    assert stats.blocked == 1
    assert stats.fetched == 1
    assert len(records) == 1


@pytest.mark.asyncio
async def test_max_items_caps_discovery():
    adapter = FakeAdapter(urls=[f"https://example.com/{i}" for i in range(100)])
    stats, records = await run_adapter(adapter, max_concurrency=10, max_items=5)
    assert stats.discovered == 5
