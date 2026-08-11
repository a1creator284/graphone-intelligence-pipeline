from __future__ import annotations

import pytest

from src.config.settings import MAX_CLOCK_SKEW_TOLERANCE_SECONDS, Settings


def test_clock_skew_tolerance_default_is_60_seconds():
    assert Settings().clock_skew_tolerance_seconds == 60


def test_clock_skew_tolerance_overridable():
    assert Settings(clock_skew_tolerance_seconds=120).clock_skew_tolerance_seconds == 120


def test_clock_skew_tolerance_accepts_value_at_hard_cap():
    settings = Settings(clock_skew_tolerance_seconds=MAX_CLOCK_SKEW_TOLERANCE_SECONDS)
    assert settings.clock_skew_tolerance_seconds == MAX_CLOCK_SKEW_TOLERANCE_SECONDS


def test_clock_skew_tolerance_rejects_values_beyond_hard_cap():
    """Requirement 4.10: SHALL NOT exceed 300 seconds."""
    with pytest.raises(ValueError):
        Settings(clock_skew_tolerance_seconds=MAX_CLOCK_SKEW_TOLERANCE_SECONDS + 1)


def test_clock_skew_tolerance_rejects_negative_values():
    with pytest.raises(ValueError):
        Settings(clock_skew_tolerance_seconds=-1)


def test_freshness_window_hours_default_is_24():
    """Pre-existing field (Task 1) -- confirming Task 3 didn't disturb it."""
    assert Settings().freshness_window_hours == 24
