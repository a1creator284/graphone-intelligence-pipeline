from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.extraction.dates import (
    extract_json_ld_date,
    extract_meta_date,
    extract_publication_date,
    extract_time_tag_date,
    is_fresh,
    parse_absolute_date,
    parse_relative_date,
    to_utc,
)

REF = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


# --- absolute dates ---------------------------------------------------------


def test_parse_iso8601_utc():
    assert parse_absolute_date("2026-08-09T12:30:00Z") == datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc)


def test_parse_absolute_with_explicit_offset():
    dt = parse_absolute_date("2026-08-09T12:30:00-05:00")
    assert dt == datetime(2026, 8, 9, 17, 30, tzinfo=timezone.utc)


def test_parse_human_readable_date():
    dt = parse_absolute_date("Aug 9, 2026")
    assert dt.date() == datetime(2026, 8, 9).date()


def test_parse_malformed_date_returns_none():
    assert parse_absolute_date("not a date at all") is None


def test_parse_empty_string_returns_none():
    assert parse_absolute_date("") is None
    assert parse_absolute_date(None) is None


# --- relative dates ----------------------------------------------------------


def test_relative_two_hours_ago():
    dt = parse_relative_date("2 hours ago", reference_time=REF)
    assert dt == REF - timedelta(hours=2)


def test_relative_thirty_minutes_ago():
    dt = parse_relative_date("30 minutes ago", reference_time=REF)
    assert dt == REF - timedelta(minutes=30)


def test_relative_yesterday():
    dt = parse_relative_date("yesterday", reference_time=REF)
    assert dt == REF - timedelta(days=1)


def test_relative_today():
    dt = parse_relative_date("today", reference_time=REF)
    assert dt == REF


def test_relative_is_deterministic_against_reference_not_wallclock():
    ref1 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ref2 = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert parse_relative_date("2 hours ago", reference_time=ref1) == ref1 - timedelta(hours=2)
    assert parse_relative_date("2 hours ago", reference_time=ref2) == ref2 - timedelta(hours=2)


def test_relative_garbage_returns_none():
    assert parse_relative_date("banana", reference_time=REF) is None


# --- timezone normalization ----------------------------------------------------


def test_naive_datetime_assumed_utc():
    naive = datetime(2026, 8, 9, 12, 0, 0)
    assert to_utc(naive) == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def test_aware_datetime_converted_to_utc():
    est = timezone(timedelta(hours=-5))
    dt = datetime(2026, 8, 9, 7, 0, 0, tzinfo=est)
    assert to_utc(dt) == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


# --- HTML extraction (JSON-LD / meta / time tag) --------------------------------


def test_extract_json_ld_date_published():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "NewsArticle", "datePublished": "2026-08-09T10:00:00Z"}
    </script>
    </head></html>
    """
    assert extract_json_ld_date(html) == datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)


def test_extract_json_ld_date_from_list():
    html = """
    <script type="application/ld+json">
    [{"@type": "Organization"}, {"@type": "Article", "datePublished": "2026-08-08T00:00:00Z"}]
    </script>
    """
    assert extract_json_ld_date(html) == datetime(2026, 8, 8, tzinfo=timezone.utc)


def test_extract_json_ld_malformed_returns_none():
    html = '<script type="application/ld+json">{not valid json</script>'
    assert extract_json_ld_date(html) is None


def test_extract_meta_article_published_time():
    html = '<meta property="article:published_time" content="2026-08-07T09:00:00Z">'
    assert extract_meta_date(html) == datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc)


def test_extract_time_tag_datetime_attr():
    html = '<time datetime="2026-08-06T08:00:00Z">Aug 6</time>'
    assert extract_time_tag_date(html) == datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)


# --- full priority chain ------------------------------------------------------


def test_priority_chain_prefers_structured_over_html():
    html = '<meta property="article:published_time" content="2026-01-01T00:00:00Z">'
    result = extract_publication_date(
        html=html, structured_value="2026-08-09T00:00:00Z", reference_time=REF
    )
    assert result == datetime(2026, 8, 9, tzinfo=timezone.utc)


def test_priority_chain_falls_back_to_html_when_no_structured_value():
    html = '<meta property="article:published_time" content="2026-08-05T00:00:00Z">'
    result = extract_publication_date(html=html, reference_time=REF)
    assert result == datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_priority_chain_falls_back_to_visible_text_relative():
    result = extract_publication_date(html="<html></html>", visible_text="3 hours ago", reference_time=REF)
    assert result == REF - timedelta(hours=3)


def test_priority_chain_falls_back_to_source_heuristic():
    fallback_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = extract_publication_date(
        html="<html></html>", reference_time=REF, source_heuristic=lambda: fallback_date
    )
    assert result == fallback_date


def test_priority_chain_returns_none_when_nothing_matches():
    """Section 13: reject the record rather than fabricate a timestamp."""
    result = extract_publication_date(html="<html><body>no dates here</body></html>", reference_time=REF)
    assert result is None


# --- 24-hour freshness boundaries ------------------------------------------------


def test_is_fresh_just_now():
    assert is_fresh(REF, REF, window_hours=24) is True


def test_is_fresh_23_hours_59_minutes_is_fresh():
    ts = REF - timedelta(hours=23, minutes=59)
    assert is_fresh(ts, REF, window_hours=24) is True


def test_is_fresh_exactly_24_hours_is_fresh_boundary_inclusive():
    ts = REF - timedelta(hours=24)
    assert is_fresh(ts, REF, window_hours=24) is True


def test_is_fresh_24_hours_1_second_is_stale():
    ts = REF - timedelta(hours=24, seconds=1)
    assert is_fresh(ts, REF, window_hours=24) is False


def test_is_fresh_handles_naive_and_aware_consistently():
    naive_ts = REF.replace(tzinfo=None) - timedelta(hours=1)
    assert is_fresh(naive_ts, REF, window_hours=24) is True


def test_is_fresh_across_timezones():
    est = timezone(timedelta(hours=-5))
    # 8am EST == 1pm UTC, 1 hour before REF (12pm... wait REF is noon UTC)
    ts_est = datetime(2026, 8, 10, 6, 0, 0, tzinfo=est)  # == 11:00 UTC, 1hr before REF
    assert is_fresh(ts_est, REF, window_hours=24) is True


def test_is_fresh_small_future_skew_tolerated():
    ts = REF + timedelta(minutes=2)
    assert is_fresh(ts, REF, window_hours=24) is True


def test_is_fresh_large_future_timestamp_rejected():
    ts = REF + timedelta(hours=1)
    assert is_fresh(ts, REF, window_hours=24) is False


def test_is_fresh_custom_window():
    ts = REF - timedelta(hours=2)
    assert is_fresh(ts, REF, window_hours=1) is False
    assert is_fresh(ts, REF, window_hours=3) is True
