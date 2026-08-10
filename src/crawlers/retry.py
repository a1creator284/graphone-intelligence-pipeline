"""
Bounded async retry with exponential backoff + full jitter.

Used by both the HTTP crawler (crawlers/http.py) and the LLM orchestrator
(llm/retry.py) so retry semantics are consistent across the codebase.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from src.config.logging import get_logger
from src.errors import PayloadTooLargeError, RateLimitError

T = TypeVar("T")
logger = get_logger(component="retry")


def compute_backoff_seconds(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    retry_after: float | None = None,
) -> float:
    """attempt is 1-indexed. Uses "full jitter": random(0, min(max, base*2^attempt))."""
    if retry_after is not None:
        # Respect an explicit Retry-After even if it's larger than our cap --
        # the server told us exactly what it wants.
        return max(retry_after, 0.0)
    ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    retryable_exceptions: tuple[type[Exception], ...],
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Run `fn` with bounded retries. Never retries forever -- after
    max_attempts the last exception propagates so callers (e.g. the LLM
    orchestrator) can fall back to another provider instead of hanging.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            retry_after = getattr(exc, "retry_after_seconds", None) if isinstance(exc, RateLimitError) else None
            delay = compute_backoff_seconds(attempt, base_delay=base_delay, max_delay=max_delay, retry_after=retry_after)
            if on_retry:
                on_retry(attempt, exc, delay)
            logger.info(
                "retry_scheduled",
                attempt=attempt,
                max_attempts=max_attempts,
                delay_seconds=round(delay, 2),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(delay)
        except PayloadTooLargeError:
            # 413 is never solved by retrying the same payload -- caller must
            # chunk first. Propagate immediately rather than wasting attempts.
            raise
    assert last_exc is not None
    raise last_exc
