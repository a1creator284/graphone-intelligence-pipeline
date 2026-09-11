"""
Mocked (non-live) tests for AsyncHttpClient status-code classification.
Covers the real bug found via the GitHub integration test: some APIs return
403 (not 429) for rate-limit exhaustion, signaled via X-RateLimit-Remaining.
"""
from __future__ import annotations

import httpx
import pytest

from src.crawlers.http import AsyncHttpClient
from src.errors import BlockedSourceError, RateLimitError


def _mock_transport(status_code: int, headers: dict[str, str] | None = None, text: str = "{}"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers or {}, text=text)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_403_with_rate_limit_remaining_zero_is_rate_limit_error():
    client = AsyncHttpClient(max_concurrency=1, timeout_seconds=5, max_retries=1)
    client._client = httpx.AsyncClient(
        transport=_mock_transport(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"})
    )
    with pytest.raises(RateLimitError):
        await client.get("https://api.github.com/repos/x/y")
    await client.aclose()


@pytest.mark.asyncio
async def test_403_without_rate_limit_headers_is_blocked_source_error():
    client = AsyncHttpClient(max_concurrency=1, timeout_seconds=5, max_retries=1)
    client._client = httpx.AsyncClient(transport=_mock_transport(403))
    with pytest.raises(BlockedSourceError):
        await client.get("https://example.com/forbidden")
    await client.aclose()


@pytest.mark.asyncio
async def test_401_is_blocked_source_error():
    client = AsyncHttpClient(max_concurrency=1, timeout_seconds=5, max_retries=1)
    client._client = httpx.AsyncClient(transport=_mock_transport(401))
    with pytest.raises(BlockedSourceError):
        await client.get("https://example.com/auth-required")
    await client.aclose()


@pytest.mark.asyncio
async def test_200_returns_fetch_result_with_content_hash():
    client = AsyncHttpClient(max_concurrency=1, timeout_seconds=5, max_retries=1)
    client._client = httpx.AsyncClient(transport=_mock_transport(200, text="hello world"))
    result = await client.get("https://example.com/ok")
    assert result.status_code == 200
    assert result.text == "hello world"
    assert len(result.content_hash) == 64
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("header,expected_delay", [
    ("4", 4), ("Thu, 01 Jan 1970 00:00:15 GMT", 5), ("invalid", 1), ("nan", 1),
])
async def test_429_http_retry_after_and_recovery(respx_mock, monkeypatch, method, header, expected_delay):
    from unittest.mock import AsyncMock
    sleep = AsyncMock()
    monkeypatch.setattr("src.crawlers.retry.asyncio.sleep", sleep)
    monkeypatch.setattr("src.crawlers.retry.random.uniform", lambda low, high: high)
    monkeypatch.setattr("src.crawlers.retry.time.time", lambda: 10)
    route = respx_mock.route(method=method.upper(), url="https://example.com/retry").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": header}), httpx.Response(200, text="ok")]
    )
    async with AsyncHttpClient(max_retries=3) as client:
        client._base_delay, client._max_delay = 1, 30
        kwargs = {"json": {"input": "same"}, "retry": True} if method == "post" else {}
        result = await getattr(client, method)("https://example.com/retry", **kwargs)
    assert result.text == "ok"
    assert route.call_count == 2
    sleep.assert_awaited_once_with(expected_delay)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post"])
async def test_http_429_exhaustion_is_bounded(respx_mock, monkeypatch, method):
    from unittest.mock import AsyncMock
    sleep = AsyncMock()
    monkeypatch.setattr("src.crawlers.retry.asyncio.sleep", sleep)
    route = respx_mock.route(method=method.upper(), url="https://example.com/limited").respond(429)
    async with AsyncHttpClient(max_retries=3) as client:
        with pytest.raises(RateLimitError):
            await getattr(client, method)("https://example.com/limited", **({"retry": True} if method == "post" else {}))
    assert route.call_count == 3
    assert sleep.await_count == 2
