"""
Freshness validation (Phase 6, Task 3 -- design.md "Freshness Validation
Architecture", requirements.md Requirement 4).

Enforces a strict freshness window (24 hours by default) with a small,
configurable clock-skew tolerance for "just published" server clock drift.
This is a pure function -- it takes reference_time explicitly rather than
calling datetime.now() itself, so freshness decisions are deterministic and
testable against a frozen point in time (Section 15 convention, also used
by src/extraction/dates.py's own is_fresh()).

Note: this is intentionally a *distinct* function from
src.extraction.dates.is_fresh(). That one is a simple boolean check with a
fixed clock-skew allowance, already used/tested independently of the news
vertical. This one is the News-vertical-specific gate required by
Requirement 4: it takes a caller-configurable clock_skew_tolerance_seconds
(sourced from Settings, capped per Requirement 4.10) and returns a
(bool, rejection_reason) tuple so the pipeline can log and count a specific
rejection category -- stale vs. future-dated -- rather than a bare
True/False (Requirement 4.7-4.9).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.extraction.dates import to_utc


def is_fresh(
    published_at: datetime,
    reference_time: datetime,
    window_hours: int = 24,
    clock_skew_tolerance_seconds: int = 60,
) -> tuple[bool, str | None]:
    """Check whether `published_at` falls within the freshness window.

    Returns (True, None) when:
        (reference_time - window_hours) <= published_at <= (reference_time + clock_skew_tolerance)

    Otherwise returns (False, rejection_reason), where rejection_reason is
    one of:
        "stale_record: ..."   -- published_at is before the window
        "future_dated: ..."   -- published_at is beyond clock-skew tolerance

    Naive/non-UTC datetimes are normalized via to_utc() before comparison
    (never guesses an offset that wasn't provided -- see dates.py).
    """
    published_at = to_utc(published_at)
    reference_time = to_utc(reference_time)

    oldest_acceptable = reference_time - timedelta(hours=window_hours)
    newest_acceptable = reference_time + timedelta(seconds=clock_skew_tolerance_seconds)

    if published_at < oldest_acceptable:
        age_hours = (reference_time - published_at).total_seconds() / 3600
        return False, f"stale_record: published {age_hours:.1f}h before reference_time"

    if published_at > newest_acceptable:
        delta_seconds = (published_at - reference_time).total_seconds()
        return False, (
            f"future_dated: published {delta_seconds:.0f}s ahead of reference_time "
            f"(exceeds {clock_skew_tolerance_seconds}s tolerance)"
        )

    return True, None
