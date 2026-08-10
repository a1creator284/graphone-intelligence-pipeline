"""
Deterministic date extraction engine (Section 14).

Priority order for extracting a publication date from a document:
  1. JSON-LD `datePublished`
  2. OpenGraph / article meta tags (article:published_time, og:updated_time)
  3. generic <meta> date fields (date, dc.date, sailthru.date, ...)
  4. <time datetime="...">
  5. structured source metadata (passed in directly by an adapter, e.g. an
     API's own `published_at` field -- highest-trust when available, so
     callers should try this BEFORE falling back to HTML parsing)
  6. visible date text (last resort, regex over plain text)
  7. relative date expressions ("2 hours ago", "yesterday")
  8. source-specific heuristic (adapter-supplied callback)

Everything is normalized to UTC ISO-8601. Nothing here ever invents a date --
if no strategy succeeds, the result is None and callers must not guess
(Sections 12/13: reject the record rather than fabricate a timestamp).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import dateparser
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

_RELATIVE_PATTERN = re.compile(
    r"^\s*(?P<amount>\d+)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago\s*$",
    re.IGNORECASE,
)
_YESTERDAY_PATTERN = re.compile(r"^\s*yesterday\s*$", re.IGNORECASE)
_TODAY_PATTERN = re.compile(r"^\s*today\s*$", re.IGNORECASE)

_UNIT_TO_TIMEDELTA_KW = {
    "second": "seconds",
    "minute": "minutes",
    "hour": "hours",
    "day": "days",
    "week": "weeks",
}


def to_utc(dt: datetime) -> datetime:
    """Normalize any timezone-aware or naive datetime to UTC.

    Naive datetimes are assumed to already be UTC (never guess a timezone
    offset that wasn't provided).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_absolute_date(text: str) -> datetime | None:
    """Parse an absolute date/timestamp string. Returns None (never raises)
    on anything unparseable."""
    if not text or not text.strip():
        return None
    try:
        dt = dateutil_parser.isoparse(text.strip())
        return to_utc(dt)
    except (ValueError, OverflowError):
        pass
    try:
        dt = dateutil_parser.parse(text.strip())
        return to_utc(dt)
    except (ValueError, OverflowError, TypeError):
        return None


def parse_relative_date(text: str, *, reference_time: datetime) -> datetime | None:
    """Parse relative expressions like '2 hours ago', 'yesterday', 'today'
    deterministically against a supplied reference time (never wall-clock
    `now()` implicitly -- keeps this testable/freezable, Section 15)."""
    if not text:
        return None
    text = text.strip()
    reference_time = to_utc(reference_time)

    if _TODAY_PATTERN.match(text):
        return reference_time
    if _YESTERDAY_PATTERN.match(text):
        return reference_time - timedelta(days=1)

    match = _RELATIVE_PATTERN.match(text)
    if match:
        amount = int(match.group("amount"))
        unit = match.group("unit").lower()
        if unit in _UNIT_TO_TIMEDELTA_KW:
            return reference_time - timedelta(**{_UNIT_TO_TIMEDELTA_KW[unit]: amount})
        if unit == "month":
            return reference_time - timedelta(days=30 * amount)
        if unit == "year":
            return reference_time - timedelta(days=365 * amount)

    # Fall back to dateparser's relative-date support for phrasing we don't
    # special-case above (e.g. "3 days ago" edge cases, "last week").
    settings = {
        "RELATIVE_BASE": reference_time.replace(tzinfo=None),
        "TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "past",
    }
    parsed = dateparser.parse(text, settings=settings)
    if parsed is None:
        return None
    return to_utc(parsed)


def extract_json_ld_date(html: str) -> datetime | None:
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = candidate.get("datePublished") or candidate.get("dateCreated")
            if value:
                parsed = parse_absolute_date(str(value))
                if parsed:
                    return parsed
    return None


def extract_meta_date(html: str) -> datetime | None:
    soup = BeautifulSoup(html, "lxml")
    meta_names = [
        ("property", "article:published_time"),
        ("property", "og:updated_time"),
        ("name", "date"),
        ("name", "dc.date"),
        ("name", "dc.date.issued"),
        ("name", "sailthru.date"),
        ("name", "parsely-pub-date"),
    ]
    for attr, value in meta_names:
        tag = soup.find("meta", {attr: value})
        if tag and tag.get("content"):
            parsed = parse_absolute_date(tag["content"])
            if parsed:
                return parsed
    return None


def extract_time_tag_date(html: str) -> datetime | None:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("time")
    if tag and tag.get("datetime"):
        return parse_absolute_date(tag["datetime"])
    if tag and tag.text:
        return parse_absolute_date(tag.text)
    return None


def extract_publication_date(
    *,
    html: str | None = None,
    structured_value: str | datetime | None = None,
    visible_text: str | None = None,
    reference_time: datetime,
    source_heuristic: Callable[[], datetime | None] | None = None,
) -> datetime | None:
    """Run the full priority chain and return the first successful match, or
    None if nothing in the chain could establish a date.
    """
    # 5. structured source metadata (highest trust when supplied directly)
    if structured_value is not None:
        if isinstance(structured_value, datetime):
            return to_utc(structured_value)
        parsed = parse_absolute_date(str(structured_value)) or parse_relative_date(
            str(structured_value), reference_time=reference_time
        )
        if parsed:
            return parsed

    if html:
        # 1. JSON-LD
        parsed = extract_json_ld_date(html)
        if parsed:
            return parsed
        # 2/3. OpenGraph/meta
        parsed = extract_meta_date(html)
        if parsed:
            return parsed
        # 4. <time datetime>
        parsed = extract_time_tag_date(html)
        if parsed:
            return parsed

    # 6/7. visible text -- absolute first, then relative
    if visible_text:
        parsed = parse_absolute_date(visible_text)
        if parsed:
            return parsed
        parsed = parse_relative_date(visible_text, reference_time=reference_time)
        if parsed:
            return parsed

    # 8. source-specific heuristic, last resort
    if source_heuristic is not None:
        parsed = source_heuristic()
        if parsed:
            return to_utc(parsed)

    return None


def is_fresh(timestamp: datetime, reference_time: datetime, window_hours: int = 24) -> bool:
    """Section 15: timezone-aware, UTC-normalized, deterministic freshness check."""
    ts = to_utc(timestamp)
    ref = to_utc(reference_time)
    age = ref - ts
    return timedelta(0) <= age <= timedelta(hours=window_hours) or (
        # allow a small amount of clock skew for "just now" / future-dated
        # timestamps up to 5 minutes ahead of reference_time
        -timedelta(minutes=5) <= age < timedelta(0)
    )
