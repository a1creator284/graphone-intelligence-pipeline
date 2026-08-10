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
