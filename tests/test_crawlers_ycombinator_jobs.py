from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.ycombinator_jobs import YCombinatorWhoIsHiringAdapter
from src.errors import ParsingError


class MockHttpClient:
    def __init__(self, responses: dict):
        self.responses = responses
        
    async def get(self, url: str) -> FetchResult:
        if url in self.responses:
            return FetchResult(url=url, status_code=200, text=self.responses[url], content_hash="x", headers={})
        return FetchResult(url=url, status_code=404, text="Not Found", content_hash="x", headers={})


@pytest.fixture
def search_response_valid():
    return json.dumps({
        "hits": [
            {
                "objectID": "12345",
                "created_at": "2026-08-01T15:00:00Z"
            }
        ]
    })


@pytest.fixture
def search_response_empty():
    return json.dumps({
        "hits": []
    })


@pytest.fixture
def items_response_valid():
    return json.dumps({
        "children": [
            {
                "id": 101,
                "created_at": "2026-08-02T10:00:00Z",
                "text": "Stripe <a href=\"https://stripe.com/jobs\">https://stripe.com/jobs</a> | AI Software Engineer | Remote<p>We are hiring engineers."
            },
            {
                "id": 102,
                "created_at": "2026-08-03T11:00:00Z",
                "text": "OpenAI | Research Scientist | San Francisco | <a href=\"https://openai.com\">https://openai.com</a><p>Looking for machine learning researchers."
            },
            {
                # Missing URL
                "created_at": "2026-08-04T12:00:00Z",
                "text": "NoUrlCorp | Data Scientist | Onsite<p>Apply by email."
            },
            {
                # Insufficient parts in first line
                "created_at": "2026-08-05T13:00:00Z",
                "text": "Just a random comment, no separators.<p>Here is a link <a href=\"http://example.com\">example</a>."
            },
            {
                # Deleted/Dead comment
                "created_at": "2026-08-06T14:00:00Z",
                "text": None
            },
            {
                # Empty comment text
                "created_at": "2026-08-07T15:00:00Z",
                "text": ""
            }
        ]
    })


@pytest.mark.asyncio
async def test_ycombinator_discover_finds_thread(search_response_valid):
    client = MockHttpClient({"https://hn.algolia.com/api/v1/search_by_date?query=%22Ask+HN%3A+Who+is+hiring%3F%22&tags=story%2Cauthor_whoishiring&hitsPerPage=1": search_response_valid})
    adapter = YCombinatorWhoIsHiringAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 1
    assert urls[0].url == "https://hn.algolia.com/api/v1/items/12345"
    assert urls[0].metadata["story_created_at"] == "2026-08-01T15:00:00Z"


@pytest.mark.asyncio
async def test_ycombinator_discover_empty_search(search_response_empty):
    client = MockHttpClient({"https://hn.algolia.com/api/v1/search_by_date?query=%22Ask+HN%3A+Who+is+hiring%3F%22&tags=story%2Cauthor_whoishiring&hitsPerPage=1": search_response_empty})
    adapter = YCombinatorWhoIsHiringAdapter(http_client=client)
    
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 0


@pytest.mark.asyncio
async def test_ycombinator_discover_malformed_json():
    client = MockHttpClient({"https://hn.algolia.com/api/v1/search_by_date?query=%22Ask+HN%3A+Who+is+hiring%3F%22&tags=story%2Cauthor_whoishiring&hitsPerPage=1": "{bad json"})
    adapter = YCombinatorWhoIsHiringAdapter(http_client=client)
    
    with pytest.raises(ParsingError):
        [d async for d in adapter.discover()]


@pytest.mark.asyncio
async def test_ycombinator_parse_extracts_correctly(items_response_valid):
    adapter = YCombinatorWhoIsHiringAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text=items_response_valid, content_hash="x", headers={})
    
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    
    # Out of 6 children, only the first 2 should yield a record.
    # 3 has no URL
    # 4 has no pipe separators
    # 5 is deleted
    # 6 is empty
    assert len(records) == 2
    
    job1 = records[0].data
    assert "Stripe" in job1["company"]
    assert job1["title"] == "AI Software Engineer"
    assert job1["url"] == "https://news.ycombinator.com/item?id=101"
    assert job1["company"] == "Stripe"
    assert job1["metadata_json"]["application_url"] == "https://stripe.com/jobs"
    assert job1["posted_at"] == datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)
    
    job2 = records[1].data
    assert "OpenAI" in job2["company"]
    assert job2["title"] == "Research Scientist"
    assert job2["url"] == "https://news.ycombinator.com/item?id=102"
    assert job2["posted_at"] == datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_ycombinator_parse_malformed_json_raises():
    adapter = YCombinatorWhoIsHiringAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text="{bad}", content_hash="x", headers={})
    
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_ycombinator_parse_missing_children_raises():
    adapter = YCombinatorWhoIsHiringAdapter(http_client=None)
    fetch_result = FetchResult(url="x", status_code=200, text='{"hits": []}', content_hash="x", headers={})
    
    with pytest.raises(ParsingError):
        await adapter.parse(fetch_result, DiscoveredUrl(url="x"))


async def test_hn_uses_comment_time_and_explicit_role_not_location():
    child = {"id": 999, "created_at": "2026-09-10T10:00:00+02:00",
             "text": "Fixture Co | Remote (US) | ML Engineer | Full time<p>AI models"}
    fetched = FetchResult("https://hn.algolia.com/api/v1/items/123", 200, json.dumps({"children": [child]}), "fixture", {})
    records = await YCombinatorWhoIsHiringAdapter(None).parse(fetched, DiscoveredUrl(fetched.url, metadata={"story_created_at": "2026-09-01T10:00:00Z"}))
    assert len(records) == 1
    assert records[0].data["title"] == "ML Engineer"
    assert records[0].data["posted_at"] == datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
    assert records[0].source_url == "https://news.ycombinator.com/item?id=999"
    assert records[0].data["metadata_json"]["raw_record"] == child
    assert records[0].data["metadata_json"]["date_field"] == "created_at"


@pytest.mark.parametrize("text", ["Fixture | Remote | Full Time<p>AI company", "Fixture | Marketing Manager<p>Email campaigns", "AI company hiring!"])
async def test_hn_ambiguous_or_non_ai_postings_are_not_invented(text):
    fetched = FetchResult("https://hn.algolia.com/api/v1/items/123", 200, json.dumps({"children": [{"id": 999, "created_at": "2026-09-10T10:00:00Z", "text": text}]}), "fixture", {})
    assert await YCombinatorWhoIsHiringAdapter(None).parse(fetched, DiscoveredUrl(fetched.url)) == []
