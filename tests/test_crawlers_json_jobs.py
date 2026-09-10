from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.remoteok import RemoteOKAIAdapter
from src.crawlers.workingnomads import WorkingNomadsAIAdapter
from src.errors import ParsingError


# --- RemoteOK Tests ---

@pytest.fixture
def remoteok_sample_payload():
    return [
        {"legal": "some text"},
        {
            "id": "1",
            "position": "AI Engineer",
            "company": "Tech Corp",
            "url": "https://remoteok.com/l/123",
            "date": "2026-08-09T10:00:00+00:00",
        },
        {
            "id": "2",
            "position": "Data Scientist",
            "company": "Data Inc",
            "url": "https://remoteok.com/l/456?utm_source=foo",
            "date": "2026-08-10T12:00:00Z",
        },
        {
            "id": "3",
            # Missing position, should be skipped
            "company": "Missing Title Inc",
            "url": "https://remoteok.com/l/789",
            "date": "2026-08-11T12:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_remoteok_discover_yields_correct_url():
    adapter = RemoteOKAIAdapter(http_client=None)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 1
    assert urls[0].url == "https://remoteok.com/api?tags=ai,ml"


@pytest.mark.asyncio
async def test_remoteok_parse_valid_and_multiple_jobs(remoteok_sample_payload):
    adapter = RemoteOKAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=json.dumps(remoteok_sample_payload), content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    
    # One legal block, one invalid job, two valid jobs => 2 records
    assert len(records) == 2
    
    job1 = records[0].data
    assert job1["title"] == "AI Engineer"
    assert job1["company"] == "Tech Corp"
    assert job1["url"] == "https://remoteok.com/l/123"
    assert job1["posted_at"] == datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    assert job1["is_remote"] is True

    job2 = records[1].data
    assert job2["title"] == "Data Scientist"
    # url normalization should strip utm_source
    assert job2["url"] == "https://remoteok.com/l/456"
    assert job2["posted_at"] == datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_remoteok_parse_missing_date_allowed():
    payload = [{"position": "AI Engineer", "company": "Foo", "url": "https://foo.com"}]
    adapter = RemoteOKAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=json.dumps(payload), content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 1
    assert records[0].data["posted_at"] is None


@pytest.mark.asyncio
async def test_remoteok_parse_malformed_json_raises():
    adapter = RemoteOKAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="{not json}", content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_remoteok_parse_empty_response():
    adapter = RemoteOKAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="[]", content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records == []


@pytest.mark.asyncio
async def test_remoteok_parse_not_a_list():
    adapter = RemoteOKAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text='{"error": "bad"}', content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


# --- WorkingNomads Tests ---

@pytest.fixture
def workingnomads_sample_payload():
    return [
        {
            "id": "1",
            "title": "AI Data Engineer",
            "company_name": "Nomad Corp",
            "url": "https://www.workingnomads.com/jobs/data-engineer",
            "pub_date": "2026-08-09T10:00:00+00:00",
        },
        {
            "id": "2",
            "title": "Machine Learning Engineer",
            "company_name": "AI Startup",
            "url": "https://www.workingnomads.com/jobs/ml-engineer?utm_campaign=xyz",
            "pub_date": "2026-08-10T15:00:00Z",
        },
        {
            "id": "3",
            # Missing url, should be skipped
            "title": "Missing URL Job",
            "company_name": "Bad Data Inc",
            "pub_date": "2026-08-11T12:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_workingnomads_discover_yields_correct_url():
    adapter = WorkingNomadsAIAdapter(http_client=None)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 1
    assert urls[0].url == "https://www.workingnomads.com/api/exposed_jobs?category=data"


@pytest.mark.asyncio
async def test_workingnomads_parse_valid_and_multiple_jobs(workingnomads_sample_payload):
    adapter = WorkingNomadsAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=json.dumps(workingnomads_sample_payload), content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    
    assert len(records) == 2
    
    job1 = records[0].data
    assert job1["title"] == "AI Data Engineer"
    assert job1["company"] == "Nomad Corp"
    assert job1["url"] == "https://www.workingnomads.com/jobs/data-engineer"
    assert job1["posted_at"] == datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    assert job1["is_remote"] is True

    job2 = records[1].data
    assert job2["title"] == "Machine Learning Engineer"
    # url normalization should strip tracking params
    assert job2["url"] == "https://www.workingnomads.com/jobs/ml-engineer"
    assert job2["posted_at"] == datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_workingnomads_parse_missing_date_allowed():
    payload = [{"title": "AI Engineer", "company_name": "Foo", "url": "https://foo.com"}]
    adapter = WorkingNomadsAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=json.dumps(payload), content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert len(records) == 1
    assert records[0].data["posted_at"] is None


@pytest.mark.asyncio
async def test_workingnomads_parse_malformed_json_raises():
    adapter = WorkingNomadsAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="{bad}", content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_workingnomads_parse_empty_response():
    adapter = WorkingNomadsAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="[]", content_hash="x", headers={})
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    assert records == []


@pytest.mark.asyncio
async def test_workingnomads_parse_not_a_list():
    adapter = WorkingNomadsAIAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text='{"error": "bad"}', content_hash="x", headers={})
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
