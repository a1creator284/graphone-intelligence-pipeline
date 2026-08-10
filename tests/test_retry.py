from __future__ import annotations

import pytest

from src.crawlers.retry import compute_backoff_seconds, retry_async
from src.errors import NetworkError, PayloadTooLargeError, RateLimitError


def test_backoff_respects_retry_after_header():
    delay = compute_backoff_seconds(1, base_delay=1, max_delay=30, retry_after=12.5)
    assert delay == 12.5


def test_backoff_grows_exponentially_and_stays_capped():
    for attempt in range(1, 10):
        delay = compute_backoff_seconds(attempt, base_delay=1, max_delay=20)
        assert 0 <= delay <= 20


@pytest.mark.asyncio
async def test_retry_eventually_succeeds_after_transient_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise NetworkError("boom")
        return "ok"

    result = await retry_async(
        flaky, max_attempts=5, base_delay=0.001, max_delay=0.01, retryable_exceptions=(NetworkError,)
    )
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    async def always_fails():
        raise RateLimitError("still 429")

    with pytest.raises(RateLimitError):
        await retry_async(
            always_fails, max_attempts=3, base_delay=0.001, max_delay=0.01, retryable_exceptions=(RateLimitError,)
        )


@pytest.mark.asyncio
async def test_429_uses_retry_after_from_exception():
    calls = {"n": 0}
    delays_seen = []

    async def rate_limited_then_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("429", retry_after_seconds=0.001)
        return "ok"

    result = await retry_async(
        rate_limited_then_ok,
        max_attempts=3,
        base_delay=0.001,
        max_delay=0.01,
        retryable_exceptions=(RateLimitError,),
        on_retry=lambda attempt, exc, delay: delays_seen.append(delay),
    )
    assert result == "ok"
    assert delays_seen == [0.001]


@pytest.mark.asyncio
async def test_413_is_never_retried():
    """413 requires chunking, not retrying the same oversized payload."""
    calls = {"n": 0}

    async def too_large():
        calls["n"] += 1
        raise PayloadTooLargeError("413")

    with pytest.raises(PayloadTooLargeError):
        await retry_async(
            too_large, max_attempts=5, base_delay=0.001, max_delay=0.01, retryable_exceptions=(NetworkError, RateLimitError)
        )
    assert calls["n"] == 1  # no retries attempted
