"""
Async HTTP client wrapper: connection pooling, bounded concurrency, timeouts,
and typed error translation. This is the ONLY place raw httpx calls happen --
adapters never call requests.get()/httpx directly in a loop (Section 9).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

import httpx

from src.config.logging import get_logger
from src.config.settings import get_settings
from src.crawlers.retry import parse_retry_after, retry_async
from src.errors import BlockedSourceError, NetworkError, PayloadTooLargeError, RateLimitError, TimeoutErrorPipeline

logger = get_logger(component="http_crawler")

DEFAULT_USER_AGENT = "GraphOneIntelligenceBot/1.0 (+https://example.com/bot; contact=engineering@example.com)"


@dataclass(slots=True)
class FetchResult:
    url: str
    status_code: int
    text: str
    content_hash: str
    headers: dict[str, str]


class AsyncHttpClient:
    """Wraps httpx.AsyncClient with bounded concurrency + retry + typed errors.

    One instance should be shared across an entire crawl run so the
    connection pool and the concurrency semaphore are actually effective.
    """

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        settings = get_settings()
        self._max_concurrency = max_concurrency or settings.max_concurrency
        self._timeout = timeout_seconds or settings.request_timeout_seconds
        self._max_retries = max_retries or settings.max_retries
        self._base_delay = settings.retry_base_delay_seconds
        self._max_delay = settings.retry_max_delay_seconds
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=self._max_concurrency * 2, max_keepalive_connections=self._max_concurrency),
        )

    async def __aenter__(self) -> "AsyncHttpClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> FetchResult:
        async def _do_request() -> FetchResult:
            async with self._semaphore:
                try:
                    response = await self._client.get(url, headers=headers)
                except httpx.TimeoutException as exc:
                    raise TimeoutErrorPipeline(f"Timed out fetching {url}", context={"url": url}) from exc
                except httpx.TransportError as exc:
                    raise NetworkError(f"Network error fetching {url}: {exc}", context={"url": url}) from exc

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise RateLimitError(
                    f"429 from {url}",
                    retry_after_seconds=parse_retry_after(retry_after),
                    context={"url": url},
                )
            if response.status_code == 413:
                raise PayloadTooLargeError(f"413 from {url}", context={"url": url})
            if response.status_code == 403:
                # Some APIs (notably GitHub) signal rate-limit exhaustion via
                # 403 + X-RateLimit-Remaining: 0 rather than a real 429. Treat
                # that as retryable/rate-limited; anything else 403 is a
                # genuine access block we do not attempt to bypass.
                remaining = response.headers.get("X-RateLimit-Remaining") or response.headers.get("x-ratelimit-remaining")
                reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("x-ratelimit-reset")
                if remaining == "0":
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    if retry_after is None and reset:
                        epoch = parse_retry_after(reset)
                        retry_after = max(0.0, epoch - time.time()) if epoch is not None else None
                    raise RateLimitError(
                        f"403 rate-limit-exhausted from {url}",
                        retry_after_seconds=retry_after,
                        context={"url": url},
                    )
                raise BlockedSourceError(
                    f"403 from {url} -- treating as blocked, not retrying/bypassing",
                    context={"url": url, "status_code": response.status_code},
                )
            if response.status_code == 401:
                raise BlockedSourceError(
                    f"401 from {url} -- treating as blocked, not retrying/bypassing",
                    context={"url": url, "status_code": response.status_code},
                )
            if response.status_code >= 500:
                raise NetworkError(f"{response.status_code} server error from {url}", context={"url": url})

            content_type = response.headers.get("content-type", "")
            text = response.text
            content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

            logger.info(
                "fetch_complete",
                url=url,
                status=response.status_code,
                content_type=content_type,
                bytes=len(response.content),
            )

            return FetchResult(
                url=url,
                status_code=response.status_code,
                text=text,
                content_hash=content_hash,
                headers=dict(response.headers),
            )

        return await retry_async(
            _do_request,
            max_attempts=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            retryable_exceptions=(RateLimitError, NetworkError, TimeoutErrorPipeline),
        )

    async def post(self, url: str, *, headers: dict[str, str] | None = None, json: dict | None = None, retry: bool = False) -> FetchResult:
        from src.errors import AuthenticationError, ParsingError
        
        async def _do_request() -> FetchResult:
            async with self._semaphore:
                try:
                    response = await self._client.post(url, headers=headers, json=json)
                except httpx.TimeoutException as exc:
                    raise TimeoutErrorPipeline(f"Timed out posting {url}", context={"url": url}) from exc
                except httpx.TransportError as exc:
                    raise NetworkError(f"Network error posting {url}: {exc}", context={"url": url}) from exc

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise RateLimitError(
                    f"429 from {url}",
                    retry_after_seconds=parse_retry_after(retry_after),
                    context={"url": url},
                )
            if response.status_code == 413:
                raise PayloadTooLargeError(f"413 from {url}", context={"url": url})
            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining") or response.headers.get("x-ratelimit-remaining")
                reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("x-ratelimit-reset")
                if remaining == "0":
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    if retry_after is None and reset:
                        epoch = parse_retry_after(reset)
                        retry_after = max(0.0, epoch - time.time()) if epoch is not None else None
                    raise RateLimitError(
                        f"403 rate-limit-exhausted from {url}",
                        retry_after_seconds=retry_after,
                        context={"url": url},
                    )
                raise AuthenticationError(
                    f"403 from {url} -- treating as auth failure",
                    context={"url": url, "status_code": response.status_code},
                )
            if response.status_code == 401:
                raise AuthenticationError(
                    f"401 from {url} -- treating as auth failure",
                    context={"url": url, "status_code": response.status_code},
                )
            if response.status_code >= 500:
                raise NetworkError(f"{response.status_code} server error from {url}", context={"url": url})

            content_type = response.headers.get("content-type", "")
            text = response.text
            content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

            logger.info(
                "post_complete",
                url=url,
                status=response.status_code,
                content_type=content_type,
                bytes=len(response.content),
            )

            return FetchResult(
                url=url,
                status_code=response.status_code,
                text=text,
                content_hash=content_hash,
                headers=dict(response.headers),
            )

        if retry:
            return await retry_async(
                _do_request,
                max_attempts=self._max_retries,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
                retryable_exceptions=(RateLimitError, NetworkError, TimeoutErrorPipeline),
            )
        return await _do_request()
