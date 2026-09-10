"""Deterministic synthetic fixtures only; never used as live fallback data."""
from datetime import datetime, timedelta, timezone
from html import escape
import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from sqlalchemy import select

from src.config.settings import get_settings
from src.config.sources import SOURCE_REGISTRY, Vertical, get_sources_for_vertical
from src.crawlers.base import DiscoveredUrl, ParsedRecord
from src.crawlers.hackernews import HackerNewsAIAdapter
from src.crawlers.http import FetchResult
from src.crawlers.techcrunch import TechCrunchAIAdapter
from src.pipeline.news import ADAPTER_CLASSES, run_news_pipeline
from src.pipeline.workers import RunStats
from src.storage.models import News, RawDocument, ProcessingError
from src.validation.news_dates import extract_news_publication, parse_news_timestamp

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
FRESH = NOW - timedelta(hours=1)
BODY = "Synthetic test article body about artificial intelligence and model evaluation. " * 10


def article(date=FRESH, name="test"):
    meta = f'<meta property="article:published_time" content="{date.isoformat()}">' if date else ''
    return f'<html><head>{meta}<title>{name}</title></head><body><article><h1>{name}</h1><p>{BODY}</p></article></body></html>'


def rss(name, date=FRESH, duplicate=False):
    item = f'<item><title>AI {name}</title><link>https://news.test/{name}</link><pubDate>{date.isoformat()}</pubDate></item>'
    return f'<rss version="2.0"><channel><title>Test</title>{item * (2 if duplicate else 1)}</channel></rss>'


def mock_sources(router, *, fail_source=None, fail_status=503, fail_body="failure", js_source=None):
    for cls in ADAPTER_CLASSES:
        name = cls.name
        if cls is HackerNewsAIAdapter:
            route = router.get(HackerNewsAIAdapter.BASE_URL)
            response = httpx.Response(200, json={"hits": [{"title": "AI test report", "url": f"https://news.test/{name}", "objectID": "123", "created_at": NOW.isoformat()}]})
        else:
            route = router.get(cls.feed_url)
            response = httpx.Response(200, text=rss(name, duplicate=name == "techcrunch_ai_rss"))
        route.mock(return_value=httpx.Response(fail_status, text=fail_body) if name == fail_source else response)
        html = '<html><body><script>renderArticle()</script></body></html>' if name == js_source else article(name=name)
        router.get(f"https://news.test/{name}").respond(200, text=html)


@pytest.mark.parametrize("value", [None, "", "garbage", "today", "12:30", "2026-09-10", "2026-09-10T12:00:00", "Sep 10", 1789041600, True, {}, "2026-02-30T12:00:00Z", datetime(2026, 9, 10)])
def test_missing_invalid_or_incomplete_publication_rejected(value):
    assert parse_news_timestamp(value) is None


@pytest.mark.parametrize("value", ["2026-09-10T12:00:00Z", "2026-09-10T08:00:00-04:00", "Thu, 10 Sep 2026 12:00:00 +0000", "Thu, 10 Sep 2026 12:00:00 GMT", NOW])
def test_timestamp_normalized_without_guessing(value):
    assert parse_news_timestamp(value) == NOW


def test_modification_and_unlabelled_time_never_prove_publication():
    html = '<meta property="og:updated_time" content="2026-09-10T12:00:00Z"><time datetime="2026-09-10T12:00:00Z"></time><script type="application/ld+json">{"@type":"NewsArticle","dateModified":"2026-09-10T12:00:00Z"}</script>'
    assert extract_news_publication(html)[0] is None


def test_json_ld_graph_and_fallback_evidence():
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "NewsArticle", "datePublished": FRESH.isoformat(), "dateModified": NOW.isoformat()}]}) + '</script>'
    dt, evidence = extract_news_publication(html, feed_date="invalid")
    assert dt == FRESH
    assert evidence == {"rss_pubDate": "invalid", "selected": "json_ld.datePublished", "value": FRESH.isoformat()}
    stale = (NOW - timedelta(days=2)).isoformat()
    assert extract_news_publication(html, feed_date=stale)[0] == NOW - timedelta(days=2)


@pytest.mark.parametrize("published,expected", [(NOW, "valid_records"), (NOW - timedelta(hours=24), "valid_records"), (NOW - timedelta(hours=24, microseconds=1), "stale_records"), (NOW - timedelta(days=3), "stale_records"), (NOW + timedelta(microseconds=1), "future_dated_records"), (None, "invalid_records"), ("garbage", "invalid_records"), ("2026-09-10", "invalid_records")])
async def test_pipeline_boundary_cannot_be_relaxed_by_global_settings(db_session, monkeypatch, published, expected):
    monkeypatch.setenv("FRESHNESS_WINDOW_HOURS", "168")
    monkeypatch.setenv("CLOCK_SKEW_TOLERANCE_SECONDS", "300")
    get_settings.cache_clear()
    url = "https://news.test/boundary"
    record = ParsedRecord("NEWS", {"title": "AI test", "url": url, "published_at": published, "full_text_location": "inline:extracted_metadata.full_text", "extracted_metadata": {"full_text": BODY}}, "techcrunch_ai_rss", url, FetchResult(url, 200, BODY, "boundary-hash", {}))
    try:
        with patch("src.pipeline.news.run_adapter", new=AsyncMock(side_effect=[(RunStats(discovered=1, fetched=1, parsed_records=1), [record])] + [(RunStats(), [])] * 4)):
            result = await run_news_pipeline(db_session, target=10, reference_time=NOW)
        assert getattr(result, expected) == 1
        rows = (await db_session.execute(select(News))).scalars().all()
        assert len(rows) == (1 if expected == "valid_records" else 0)
        if rows:
            assert rows[0].collected_at.replace(tzinfo=timezone.utc) == NOW
    finally:
        get_settings.cache_clear()


async def test_real_five_adapter_path_provenance_and_rerun_dedup(db_session):
    enabled = {s.name for s in get_sources_for_vertical(Vertical.NEWS)}
    assert enabled == {cls.name for cls in ADAPTER_CLASSES}
    assert len(enabled) == 5 and "synced_review_rss" not in enabled
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router)
        first = await run_news_pipeline(db_session, target=100, reference_time=NOW)
        second = await run_news_pipeline(db_session, target=100, reference_time=NOW)
    assert first.valid_records == 5
    assert first.discovery_duplicates == second.discovery_duplicates == 1
    assert first.persisted_by_source == {name: 1 for name in enabled}
    assert second.valid_records == 0 and second.duplicates == 5
    rows = (await db_session.execute(select(News))).scalars().all()
    raw = {r.id: r for r in (await db_session.execute(select(RawDocument))).scalars()}
    assert len(rows) == len(raw) == 5
    for row in rows:
        assert row.source_name in enabled
        assert row.extracted_metadata["source_url"] == row.url
        assert row.extracted_metadata["source_name"] == row.source_name
        assert row.extracted_metadata["publication_date_candidates"]["selected"]
        assert raw[row.raw_document_id].source_url == row.url
        assert raw[row.raw_document_id].source_name == row.source_name
        age = row.collected_at - row.published_at
        assert timedelta(0) <= age <= timedelta(hours=24)


@pytest.mark.parametrize("status,body", [(200, "<html>not a feed</html>"), (200, "<rss><channel>broken"), (404, "not found"), (403, "blocked"), (429, "rate limited"), (503, "unavailable")])
async def test_feed_failure_does_not_abort_other_sources(db_session, monkeypatch, status, body):
    monkeypatch.setenv("MAX_RETRIES", "1")
    get_settings.cache_clear()
    try:
        with respx.mock(assert_all_called=False) as router:
            mock_sources(router, fail_source="techcrunch_ai_rss", fail_status=status, fail_body=body)
            result = await run_news_pipeline(db_session, target=100, reference_time=NOW)
        assert result.valid_records == 4
        assert result.persisted_by_source["techcrunch_ai_rss"] == 0
        assert "techcrunch_ai_rss" in result.source_errors
        assert len((await db_session.execute(select(ProcessingError))).scalars().all()) == 1
        assert result.blocked == (1 if status == 403 else 0)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"hits":{}}', '{"hits":null}'])
async def test_hn_api_failure_does_not_abort_other_sources(db_session, body):
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router, fail_source="hackernews_ai", fail_status=200, fail_body=body)
        result = await run_news_pipeline(db_session, target=100, reference_time=NOW)
    assert result.valid_records == 4
    assert result.fetch_failed == 1
    assert result.persisted_by_source["hackernews_ai"] == 0
    assert "hackernews_ai" in result.source_errors


async def test_js_shell_rejected_without_manufacturing_content(db_session):
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router, js_source="thedecoder_rss")
        result = await run_news_pipeline(db_session, target=100, reference_time=NOW)
    assert result.extraction_failed == 1
    assert result.valid_records == 4
    assert result.persisted_by_source["thedecoder_rss"] == 0


async def test_hn_query_and_invalid_hits():
    client = AsyncMock()
    adapter = HackerNewsAIAdapter(client, reference_time=NOW)
    query = parse_qs(urlsplit(adapter._build_query_url()).query)
    assert query["query"] == ["AI"]
    assert query["numericFilters"] == [f"created_at_i>={int((NOW-timedelta(hours=24)).timestamp())},created_at_i<={int(NOW.timestamp())}"]
    hits = [None, {}, {"title": "AI news", "url": "javascript:alert(1)"}, {"title": "Haiti report", "url": "https://news.test/not-ai"}, {"title": "AI report", "url": "https://news.test/real", "created_at": NOW.isoformat(), "objectID": "123"}]
    client.get.return_value = FetchResult(adapter.BASE_URL, 200, json.dumps({"hits": hits}), "hash", {})
    found = [d async for d in adapter.discover()]
    assert len(found) == 1 and found[0].url == "https://news.test/real"
    assert found[0].metadata["hn_story_id"] == "123"


@pytest.mark.parametrize("publisher_date", [None, NOW - timedelta(days=365)])
async def test_hn_new_submission_never_refreshes_old_or_undated_article(publisher_date):
    adapter = HackerNewsAIAdapter(AsyncMock(), reference_time=NOW)
    url = "https://news.test/repost"
    found = DiscoveredUrl(url, {"hn_title": "AI repost", "hn_created_at": NOW.isoformat(), "hn_story_id": "123"})
    records = await adapter.parse(FetchResult(url, 200, article(publisher_date), "hash", {}), found)
    assert records[0].data["published_at"] == publisher_date
    assert records[0].data["extracted_metadata"]["hn_created_at"] == NOW.isoformat()


async def test_disabled_registry_source_not_fetched(db_session, monkeypatch):
    source = next(s for s in SOURCE_REGISTRY if s.name == "thedecoder_rss")
    monkeypatch.setattr(source, "enabled", False)
    with patch("src.pipeline.news.run_adapter", new=AsyncMock(return_value=(RunStats(), []))) as run:
        result = await run_news_pipeline(db_session, reference_time=NOW)
    assert run.await_count == 4
    assert "thedecoder_rss" not in result.by_source


async def test_atom_updated_only_not_publication():
    client = AsyncMock()
    adapter = TechCrunchAIAdapter(client, reference_time=NOW)
    feed = '<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry><title>AI news</title><link href="https://news.test/atom"/><updated>2026-09-10T12:00:00Z</updated></entry></feed>'
    client.get.return_value = FetchResult(adapter.feed_url, 200, feed, "hash", {})
    found = [d async for d in adapter.discover()]
    assert found[0].metadata["rss_pubDate"] is None
    records = await adapter.parse(FetchResult(found[0].url, 200, article(None), "hash", {}), found[0])
    assert records[0].data["published_at"] is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://news.test/a", "not-a-url", "", None])
async def test_rss_and_schema_reject_non_http_urls(url):
    from src.validation.schemas import validate_news_record

    client = AsyncMock()
    adapter = TechCrunchAIAdapter(client, reference_time=NOW)
    xml = '<rss version="2.0"><channel><title>Test</title><item><title>AI test</title>'
    if url is not None:
        xml += '<link>' + escape(url) + '</link>'
    xml += '</item></channel></rss>'
    client.get.return_value = FetchResult(adapter.feed_url, 200, xml, "hash", {})
    assert [d async for d in adapter.discover()] == []
    validated, error = validate_news_record({"title": "AI test", "url": url, "source_name": adapter.name, "published_at": FRESH, "full_text_location": "inline:test"})
    assert validated is None and error


async def test_valid_empty_sources_are_not_failures_or_padded(db_session):
    with respx.mock(assert_all_called=True) as router:
        for cls in ADAPTER_CLASSES:
            if cls is HackerNewsAIAdapter:
                router.get(cls.BASE_URL).respond(200, json={"hits": []})
            else:
                router.get(cls.feed_url).respond(200, text='<rss version="2.0"><channel><title>Empty feed</title></channel></rss>')
        result = await run_news_pipeline(db_session, reference_time=NOW)
    assert result.valid_records == result.fetch_failed == result.invalid_records == 0
    assert result.source_errors == {}
    assert len(result.persisted_by_source) == 5
    assert not any(result.persisted_by_source.values())
    assert not (await db_session.execute(select(News))).scalars().all()


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), httpx.ReadTimeout("timeout")])
async def test_network_discovery_failure_isolated(db_session, monkeypatch, failure):
    monkeypatch.setenv("MAX_RETRIES", "1")
    get_settings.cache_clear()
    try:
        with respx.mock(assert_all_called=False) as router:
            mock_sources(router)
            router.get(TechCrunchAIAdapter.feed_url).mock(side_effect=failure)
            result = await run_news_pipeline(db_session, target=100, reference_time=NOW)
        assert result.valid_records == 4
        assert result.fetch_failed == 1
        assert "techcrunch_ai_rss" in result.source_errors
    finally:
        get_settings.cache_clear()
