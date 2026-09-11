"""
Bounded async retry with exponential backoff + full jitter.

Used by both the HTTP crawler (crawlers/http.py) and the LLM orchestrator
(llm/retry.py) so retry semantics are consistent across the codebase.
"""
from __future__ import annotations

import asyncio
import random
import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from collections.abc import Awaitable, Callable
from typing import TypeVar

from src.config.logging import get_logger
from src.errors import PayloadTooLargeError, RateLimitError

T = TypeVar("T")
logger = get_logger(component="retry")


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse delta-seconds or an HTTP-date; invalid/nonfinite hints are ignored."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = max(0.0, date.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def compute_backoff_seconds(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    retry_after: float | None = None,
) -> float:
    """Full jitter under an exponential ceiling; valid server hints are a floor.

    retry_async refuses hints exceeding max_delay rather than retrying early.
    """
    ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
    delay = random.uniform(0, ceiling)
    if retry_after is not None and math.isfinite(retry_after) and retry_after >= 0:
        delay = max(delay, retry_after)
    return min(max_delay, delay)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    retryable_exceptions: tuple[type[Exception], ...],
    on_retry: Callable[[int, Exception, float], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Run `fn` with bounded retries. Never retries forever -- after
    max_attempts the last exception propagates so callers (e.g. the LLM
    orchestrator) can fall back to another provider instead of hanging.
    """
    if max_attempts < 1 or not all(math.isfinite(v) and v >= 0 for v in (base_delay, max_delay)):
        raise ValueError("Retry attempts must be positive and delays finite/nonnegative")
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except PayloadTooLargeError:
            # This must precede even a broad PipelineError retry tuple.
            raise
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            retry_after = getattr(exc, "retry_after_seconds", None) if isinstance(exc, RateLimitError) else None
            if retry_after is not None and math.isfinite(retry_after) and retry_after > max_delay:
                # Do not sleep indefinitely OR violate a server's requested cooldown.
                logger.warning("retry_after_exceeds_budget", attempt=attempt, max_delay_seconds=max_delay)
                raise
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
            await (sleep or asyncio.sleep)(delay)
    assert last_exc is not None
    raise last_exc
