from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.validation.freshness import is_fresh

REF = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


# --- acceptance within the freshness window -----------------------------------


def test_accepts_article_published_just_now():
    fresh, reason = is_fresh(REF, REF)
    assert fresh is True
    assert reason is None


def test_accepts_article_within_24h_window():
    published = REF - timedelta(hours=12)
    fresh, reason = is_fresh(published, REF, window_hours=24)
    assert fresh is True
    assert reason is None


def test_accepts_article_exactly_at_24h_boundary_inclusive():
    published = REF - timedelta(hours=24)
    fresh, reason = is_fresh(published, REF, window_hours=24)
    assert fresh is True
    assert reason is None


# --- rejection of stale articles beyond 24 hours -------------------------------


def test_rejects_article_one_second_beyond_24h_window():
    published = REF - timedelta(hours=24, seconds=1)
    fresh, reason = is_fresh(published, REF, window_hours=24)
    assert fresh is False
    assert reason.startswith("stale_record")


def test_rejects_clearly_stale_article():
    published = REF - timedelta(days=3)
    fresh, reason = is_fresh(published, REF, window_hours=24)
    assert fresh is False
    assert "stale_record" in reason


# --- acceptance within clock skew tolerance (e.g. 30s ahead) -------------------


def test_accepts_article_thirty_seconds_ahead_within_default_tolerance():
    published = REF + timedelta(seconds=30)
    fresh, reason = is_fresh(published, REF, clock_skew_tolerance_seconds=60)
    assert fresh is True
    assert reason is None


def test_accepts_article_exactly_at_tolerance_boundary_inclusive():
    published = REF + timedelta(seconds=60)
    fresh, reason = is_fresh(published, REF, clock_skew_tolerance_seconds=60)
    assert fresh is True
    assert reason is None


# --- rejection of future-dated articles beyond tolerance (e.g. 5 min ahead) ----


def test_rejects_article_five_minutes_ahead_exceeding_default_tolerance():
    published = REF + timedelta(minutes=5)
    fresh, reason = is_fresh(published, REF, clock_skew_tolerance_seconds=60)
    assert fresh is False
    assert reason.startswith("future_dated")


def test_rejects_article_one_second_beyond_tolerance():
    published = REF + timedelta(seconds=61)
    fresh, reason = is_fresh(published, REF, clock_skew_tolerance_seconds=60)
    assert fresh is False
    assert "future_dated" in reason


# --- configurable window_hours and clock_skew_tolerance_seconds ---------------


def test_custom_window_hours_accepts_and_rejects_correctly():
    published = REF - timedelta(hours=2)
    assert is_fresh(published, REF, window_hours=1)[0] is False
    assert is_fresh(published, REF, window_hours=3)[0] is True


def test_custom_clock_skew_tolerance_accepts_and_rejects_correctly():
    published = REF + timedelta(seconds=90)
    assert is_fresh(published, REF, clock_skew_tolerance_seconds=60)[0] is False
    assert is_fresh(published, REF, clock_skew_tolerance_seconds=120)[0] is True


# --- timezone handling (delegates to dates.to_utc) -----------------------------


def test_naive_published_at_assumed_utc():
    naive_published = datetime(2026, 8, 10, 6, 0, 0)  # naive, 6h before REF
    fresh, _ = is_fresh(naive_published, REF, window_hours=24)
    assert fresh is True


def test_non_utc_timezone_normalized_before_comparison():
    est = timezone(timedelta(hours=-5))
    published_est = datetime(2026, 8, 10, 6, 0, 0, tzinfo=est)  # == 11:00 UTC, 1h before REF
    fresh, _ = is_fresh(published_est, REF, window_hours=24)
    assert fresh is True


# --- rejection reasons carry useful diagnostic info for logging ---------------


def test_stale_rejection_reason_includes_age_hours():
    published = REF - timedelta(hours=30)
    _, reason = is_fresh(published, REF, window_hours=24)
    assert "30.0h" in reason


def test_future_dated_rejection_reason_includes_delta_seconds_and_tolerance():
    published = REF + timedelta(seconds=200)
    _, reason = is_fresh(published, REF, clock_skew_tolerance_seconds=60)
    assert "200s" in reason
    assert "60s tolerance" in reason
