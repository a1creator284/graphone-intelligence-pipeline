from __future__ import annotations

import asyncio
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


class GatedAdapter(FakeAdapter):
    """Lazy source whose fetch or parse can be stalled without real sleeps."""

    def __init__(self, count: int, concurrency: int, stage: str = "fetch"):
        super().__init__(urls=[])
        self.count = count
        self.concurrency = concurrency
        self.stage = stage
        self.yielded = 0
        self.active = 0
        self.peak_active = 0
        self.closed = False
        self.full = asyncio.Event()
        self.release = asyncio.Event()
        self.workers = set()

    async def discover(self):
        try:
            for index in range(self.count):
                self.yielded += 1
                yield DiscoveredUrl(url=f"https://example.com/{index}")
        finally:
            self.closed = True

    async def gate(self):
        self.workers.add(asyncio.current_task())
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        if self.active == self.concurrency:
            self.full.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1

    async def fetch(self, discovered):
        if self.stage == "fetch":
            await self.gate()
        return await super().fetch(discovered)

    async def parse(self, fetched, discovered):
        if self.stage == "parse":
            await self.gate()
        return await super().parse(fetched, discovered)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("stage", ["fetch", "parse"])
async def test_lazy_discovery_and_outstanding_tasks_are_bounded(concurrency, stage):
    adapter = GatedAdapter(count=1000, concurrency=concurrency, stage=stage)
    baseline = asyncio.all_tasks()
    run = asyncio.create_task(run_adapter(adapter, max_concurrency=concurrency))
    try:
        await asyncio.wait_for(adapter.full.wait(), timeout=2)
        # Give discovery opportunities to run; blocked consumers must prevent
        # even the next generator pull, not merely the next active fetch.
        for _ in range(5):
            await asyncio.sleep(0)
        assert adapter.yielded == concurrency
        assert adapter.active == concurrency
        assert len(asyncio.all_tasks() - baseline - {run}) == concurrency

        adapter.release.set()
        stats, records = await asyncio.wait_for(run, timeout=5)
        assert stats.discovered == stats.fetched == stats.parsed_records == 1000
        assert {r.source_url for r in records} == {f"https://example.com/{i}" for i in range(1000)}
        assert adapter.peak_active <= concurrency
        assert adapter.closed
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
    assert all(worker.done() for worker in adapter.workers)


@pytest.mark.asyncio
async def test_backpressure_refills_without_waiting_for_slowest_worker():
    slow_release = asyncio.Event()
    third_started = asyncio.Event()

    class UnevenAdapter(FakeAdapter):
        async def fetch(self, discovered):
            if discovered.url.endswith("/0"):
                await slow_release.wait()
            if discovered.url.endswith("/2"):
                third_started.set()
            return await super().fetch(discovered)

    adapter = UnevenAdapter([f"https://example.com/{i}" for i in range(4)])
    run = asyncio.create_task(run_adapter(adapter, max_concurrency=2))
    try:
        await asyncio.wait_for(third_started.wait(), timeout=2)
        assert not run.done()
        slow_release.set()
        stats, records = await asyncio.wait_for(run, timeout=2)
        assert stats.fetched == len(records) == 4
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_drains_workers_and_closes_stalled_discovery():
    adapter = GatedAdapter(count=1000, concurrency=2)
    run = asyncio.create_task(run_adapter(adapter, max_concurrency=2))
    try:
        await asyncio.wait_for(adapter.full.wait(), timeout=2)
    finally:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=2)
    assert adapter.yielded == 2
    assert adapter.closed
    assert adapter.active == 0
    assert all(worker.done() for worker in adapter.workers)


@pytest.mark.asyncio
async def test_discovery_error_drains_already_started_workers():
    class BrokenDiscovery(GatedAdapter):
        async def discover(self):
            try:
                yield DiscoveredUrl(url="https://example.com/0")
                await self.full.wait()
                raise NetworkError("discovery failed")
            finally:
                self.closed = True

    adapter = BrokenDiscovery(count=1, concurrency=1)
    with pytest.raises(NetworkError, match="discovery failed"):
        await asyncio.wait_for(run_adapter(adapter, max_concurrency=2), timeout=2)
    assert adapter.closed
    assert adapter.active == 0
    assert all(worker.done() for worker in adapter.workers)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_items", [None, 2])
@pytest.mark.parametrize("stage", ["fetch", "parse"])
async def test_unexpected_worker_error_is_propagated_and_peers_are_drained(max_items, stage):
    class BrokenWorker(GatedAdapter):
        async def fail_or_block(self, discovered):
            if discovered.url.endswith("/0"):
                await self.full.wait()
                raise RuntimeError("worker failed")
            await self.gate()

        async def fetch(self, discovered):
            if self.stage == "fetch":
                await self.fail_or_block(discovered)
            return await FakeAdapter.fetch(self, discovered)

        async def parse(self, fetched, discovered):
            if self.stage == "parse":
                await self.fail_or_block(discovered)
            return await FakeAdapter.parse(self, fetched, discovered)

    adapter = BrokenWorker(count=100, concurrency=1, stage=stage)
    with pytest.raises(RuntimeError, match="worker failed"):
        await asyncio.wait_for(run_adapter(adapter, max_concurrency=2, max_items=max_items), timeout=2)
    assert adapter.yielded == 2
    assert adapter.closed
    assert adapter.active == 0
    assert all(worker.done() for worker in adapter.workers)


@pytest.mark.asyncio
async def test_max_items_includes_duplicates_and_closes_discovery_at_limit():
    adapter = GatedAdapter(count=100, concurrency=1)
    adapter.release.set()

    async def duplicates():
        try:
            for _ in range(100):
                adapter.yielded += 1
                yield DiscoveredUrl(url="https://example.com/same")
        finally:
            adapter.closed = True

    adapter.discover = duplicates
    stats, records = await run_adapter(adapter, max_concurrency=2, max_items=3)
    assert stats.discovered == adapter.yielded == 3
    assert stats.skipped_duplicate == 2
    assert stats.fetched == len(records) == 1
    assert adapter.closed


@pytest.mark.asyncio
async def test_zero_max_items_does_not_start_discovery():
    adapter = GatedAdapter(count=100, concurrency=1)
    stats, records = await run_adapter(adapter, max_concurrency=1, max_items=0)
    assert stats.discovered == adapter.yielded == 0
    assert not adapter.workers
    assert records == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"max_concurrency": 0},
    {"max_concurrency": -1},
    {"max_concurrency": 1, "max_items": -1},
])
async def test_invalid_work_limits_fail_before_discovery(kwargs):
    adapter = GatedAdapter(count=100, concurrency=1)
    with pytest.raises(ValueError):
        await run_adapter(adapter, **kwargs)
    assert adapter.yielded == 0
