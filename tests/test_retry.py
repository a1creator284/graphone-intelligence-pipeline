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


@pytest.mark.parametrize("fraction", [0.0, 0.25, 0.75, 1.0])
def test_full_jitter_uses_increasing_capped_exponential_windows(monkeypatch, fraction):
    windows = []
    def uniform(low, high):
        windows.append((low, high))
        return low + fraction * (high - low)
    monkeypatch.setattr("src.crawlers.retry.random.uniform", uniform)
    delays = [compute_backoff_seconds(i, base_delay=2, max_delay=10) for i in range(1, 6)]
    assert windows == [(0, 2), (0, 4), (0, 8), (0, 10), (0, 10)]
    assert delays == [fraction * x for x in [2, 4, 8, 10, 10]]


@pytest.mark.parametrize("header,expected", [
    ("12.5", 12.5), ("0", 0), (None, None), ("nonsense", None),
    ("nan", None), ("inf", None), ("-5", None),
    ("Thu, 01 Jan 1970 00:00:20 GMT", 10),
    ("Thu, 01 Jan 1970 00:00:01 GMT", 0),
])
def test_parse_retry_after_numeric_date_and_invalid(header, expected):
    from src.crawlers.retry import parse_retry_after
    assert parse_retry_after(header, now=10) == expected


@pytest.mark.asyncio
async def test_429_exhaustion_counts_attempts_and_fake_sleeps(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.crawlers.retry.random.uniform", lambda low, high: high * 0.75)
    failure = RateLimitError("still limited")
    call = AsyncMock(side_effect=failure)
    sleep = AsyncMock()
    with pytest.raises(RateLimitError) as error:
        await retry_async(call, max_attempts=4, base_delay=2, max_delay=5,
                          retryable_exceptions=(RateLimitError,), sleep=sleep)
    assert error.value is failure
    assert call.await_count == 4
    assert [args.args[0] for args in sleep.await_args_list] == [1.5, 3, 3.75]


@pytest.mark.asyncio
async def test_excessive_retry_after_fails_without_early_retry_or_unbounded_sleep():
    from unittest.mock import AsyncMock
    call = AsyncMock(side_effect=RateLimitError("cooldown", retry_after_seconds=3600))
    sleep = AsyncMock()
    with pytest.raises(RateLimitError):
        await retry_async(call, max_attempts=5, base_delay=1, max_delay=30,
                          retryable_exceptions=(RateLimitError,), sleep=sleep)
    assert call.await_count == 1
    sleep.assert_not_awaited()
    assert compute_backoff_seconds(1, base_delay=1, max_delay=30, retry_after=3600) <= 30


@pytest.mark.asyncio
async def test_413_not_retried_even_with_broad_retry_tuple():
    from unittest.mock import AsyncMock
    from src.errors import PipelineError
    call = AsyncMock(side_effect=PayloadTooLargeError("413"))
    sleep = AsyncMock()
    with pytest.raises(PayloadTooLargeError):
        await retry_async(call, max_attempts=5, base_delay=1, max_delay=30,
                          retryable_exceptions=(PipelineError,), sleep=sleep)
    assert call.await_count == 1
    sleep.assert_not_awaited()
