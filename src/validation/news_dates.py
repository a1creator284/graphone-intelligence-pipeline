"""News-only publication timestamps: no inferred dates or modification times."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from dateutil.parser import isoparse


def parse_news_timestamp(value: object) -> datetime | None:
    """Accept complete, timezone-explicit ISO/RFC timestamps only."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        value = value.strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})", value):
                parsed = isoparse(value)
            elif re.fullmatch(r"(?:[A-Za-z]{3},?\s+)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s+(?:[+-]\d{4}|GMT|UTC)", value):
                parsed = parsedate_to_datetime(value)
            else:
                return None
        except (ValueError, TypeError, OverflowError):
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def extract_news_publication(html: str, *, feed_date: object = None) -> tuple[datetime | None, dict]:
    """Prefer feed publication; otherwise only explicitly labelled publication fields.

    Never use dateModified, og:updated_time, an arbitrary <time>, or HN
    submission time. A fresh repost/update cannot make an old article fresh.
    Keep the selected raw value and field name for provenance.
    """
    candidates = {"rss_pubDate": feed_date, "selected": None}
    parsed = parse_news_timestamp(feed_date)
    if parsed is not None:
        return parsed, {**candidates, "selected": "rss_pubDate", "value": feed_date}

    soup = BeautifulSoup(html, "lxml")
    values = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("@graph"), list):
                nodes.extend(node["@graph"])
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(t in {"Article", "NewsArticle", "BlogPosting", "ReportageNewsArticle"} for t in types if isinstance(t, str)):
                values.append(("json_ld.datePublished", node.get("datePublished")))
    for attrs in ({"property": "article:published_time"}, {"name": "parsely-pub-date"}, {"itemprop": "datePublished"}):
        for tag in soup.find_all(attrs=attrs):
            values.append((next(iter(attrs.values())), tag.get("content") or tag.get("datetime")))
    for field, value in values:
        parsed = parse_news_timestamp(value)
        if parsed is not None:
            return parsed, {**candidates, "selected": field, "value": value}
    return None, candidates
