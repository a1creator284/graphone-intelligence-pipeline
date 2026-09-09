"""
Y Combinator startups adapter tests.

All three fixtures are REAL, unmodified responses captured live from the
public YC Algolia index (`45bwzj1sgc-dsn.algolia.net`) on 2026-09-09 -- the
same endpoint the ycombinator.com/companies directory UI itself queries:

  yc_algolia_sample_response.json
    GET /1/indexes/YCCompany_production
        ?query=&hitsPerPage=5&page=0
        &facetFilters=[["tags:Artificial Intelligence"],["batch:Winter 2024"]]

  yc_algolia_batch_facets.json
    same, hitsPerPage=0, facets=["batch"], maxValuesPerFacet=1000,
    facetFilters=[["tags:Artificial Intelligence","tags:AI",
                   "tags:Generative AI","tags:Machine Learning"]]
    -> the real batch->count facet listing used by discovery.

  yc_algolia_error_response.json
    the real 403 body returned for a bad API key.

Where a test needs a shape the live API did not hand us (a company with no
`slug`, a null `team_size`), it *derives* it by deleting or nulling a key in
the captured response and says so. No invented company metadata is ever
presented as a real fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.ycombinator_startups import (
    MAX_HITS_PER_QUERY,
    YCombinatorStartupsAdapter,
)
from src.errors import ParsingError
from src.validation.schemas import validate_startup_record

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "yc_algolia_sample_response.json").read_text()
BATCH_FACETS = (FIXTURES / "yc_algolia_batch_facets.json").read_text()
ERROR_BODY = (FIXTURES / "yc_algolia_error_response.json").read_text()


def _fetch_result(body: str, url: str = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/YCCompany_production") -> FetchResult:
    return FetchResult(
        url=url,
        status_code=200,
        text=body,
        content_hash="hash",
        headers={"content-type": "application/json"},
    )


class _StubHttpClient:
    """Returns a queued/mapped body per URL and records every URL requested."""

    def __init__(self, body: str | None = None, by_substring: dict[str, str] | None = None):
        self.body = body
        self.by_substring = by_substring or {}
        self.requested: list[str] = []

    async def get(self, url: str, *, headers=None) -> FetchResult:
        self.requested.append(url)
        for needle, body in self.by_substring.items():
            if needle in url:
                return _fetch_result(body, url=url)
        if self.body is None:
            raise AssertionError(f"unexpected URL: {url}")
        return _fetch_result(self.body, url=url)


class _FailingHttpClient:
    def __init__(self):
        self.requested: list[str] = []

    async def get(self, url: str, *, headers=None) -> FetchResult:
        self.requested.append(url)
        from src.errors import NetworkError

        raise NetworkError("boom", context={"url": url})


def _adapter(client, **kwargs) -> YCombinatorStartupsAdapter:
    return YCombinatorStartupsAdapter(client, **kwargs)


async def _collect(adapter) -> list[DiscoveredUrl]:
    return [d async for d in adapter.discover()]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parses_real_response_into_startup_records():
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))

    expected = json.loads(SAMPLE)["hits"]
    assert len(records) == len(expected)
    for record in records:
        assert record.record_type == "startup"
        assert record.source_name == "ycombinator_directory"


@pytest.mark.asyncio
async def test_field_mapping_is_verbatim_from_the_api():
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    hits = json.loads(SAMPLE)["hits"]

    for record, hit in zip(records, hits, strict=True):
        assert record.data["entity_name"] == hit["name"]
        assert record.source_url == f"https://www.ycombinator.com/companies/{hit['slug']}"
        if isinstance(hit.get("team_size"), int) and hit["team_size"] >= 0:
            assert record.data["employee_count"] == hit["team_size"]


@pytest.mark.asyncio
async def test_parsed_records_pass_the_startup_schema_unmodified():
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))

    for record in records:
        data = dict(record.data)
        data["source_name"] = record.source_name
        data["source_url"] = record.source_url
        validated, error = validate_startup_record(data)
        assert validated is not None, error


@pytest.mark.asyncio
async def test_adapter_emits_only_fields_the_startup_table_stores():
    """No extra keys, so nothing speculative can leak into persistence."""
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    for record in records:
        assert set(record.data) == {"entity_name", "employee_count"}


# ---------------------------------------------------------------------------
# Missing factual data is never fabricated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_team_size_stays_null_not_zero():
    """Derived from the real response by nulling `team_size` on one hit."""
    payload = json.loads(SAMPLE)
    payload["hits"][0]["team_size"] = None
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert records[0].data["employee_count"] is None
    assert records[0].data["employee_count"] != 0


@pytest.mark.asyncio
async def test_absent_team_size_key_stays_null():
    """Derived by deleting `team_size` entirely from one real hit."""
    payload = json.loads(SAMPLE)
    del payload["hits"][0]["team_size"]
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert records[0].data["employee_count"] is None


@pytest.mark.asyncio
async def test_non_numeric_team_size_is_nulled_not_coerced():
    payload = json.loads(SAMPLE)
    payload["hits"][0]["team_size"] = "a handful"
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert records[0].data["employee_count"] is None


@pytest.mark.asyncio
async def test_company_without_slug_is_skipped_not_given_a_guessed_url():
    """Derived by deleting `slug` from one real hit. The company name is
    present, so a URL *could* be guessed from it -- it must not be."""
    payload = json.loads(SAMPLE)
    dropped = payload["hits"][0]["name"]
    del payload["hits"][0]["slug"]
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert len(records) == len(payload["hits"]) - 1
    assert all(r.data["entity_name"] != dropped for r in records)
    assert all("/companies/" in r.source_url for r in records)


@pytest.mark.asyncio
async def test_company_without_name_is_skipped():
    payload = json.loads(SAMPLE)
    payload["hits"][0]["name"] = "   "
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert len(records) == len(payload["hits"]) - 1


@pytest.mark.asyncio
async def test_no_founders_funding_or_description_fields_are_emitted():
    """The API returns descriptions; we do not store them, and we certainly
    never synthesize founders or funding, which the API never returns."""
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    for record in records:
        for forbidden in ("founders", "funding", "valuation", "description", "founded_year"):
            assert forbidden not in record.data


# ---------------------------------------------------------------------------
# Malformed / error responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_raises_parsing_error():
    adapter = _adapter(_StubHttpClient())
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result("{not json"), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_html_error_page_raises_parsing_error():
    adapter = _adapter(_StubHttpClient())
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result("<html><body>nope</body></html>"), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_real_algolia_error_body_raises_rather_than_looking_empty():
    """A broken source must not be indistinguishable from a quiet one."""
    adapter = _adapter(_StubHttpClient())
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result(ERROR_BODY), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_non_list_hits_raises_parsing_error():
    adapter = _adapter(_StubHttpClient())
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result('{"hits": {"a": 1}}'), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_json_array_payload_raises_parsing_error():
    adapter = _adapter(_StubHttpClient())
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result("[1, 2, 3]"), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_non_dict_hits_entries_are_skipped_not_crashed():
    payload = json.loads(SAMPLE)
    payload["hits"].insert(0, "garbage")
    payload["hits"].insert(1, None)
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert len(records) == len(json.loads(SAMPLE)["hits"])


@pytest.mark.asyncio
async def test_empty_hits_list_yields_no_records():
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result('{"hits": [], "nbHits": 0}'), DiscoveredUrl(url="x"))
    assert records == []


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_hits_in_one_page_are_deduped_deterministically():
    payload = json.loads(SAMPLE)
    payload["hits"].append(dict(payload["hits"][0]))
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    urls = [r.source_url for r in records]
    assert len(urls) == len(set(urls))
    assert len(records) == len(json.loads(SAMPLE)["hits"])


@pytest.mark.asyncio
async def test_same_company_across_two_pages_yields_identical_source_url():
    """The dedup key must be stable so cross-page dedup and the DB unique
    constraint actually collapse the duplicate."""
    adapter = _adapter(_StubHttpClient())
    page_a = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="page0"))
    page_b = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="page1"))

    assert [r.source_url for r in page_a] == [r.source_url for r in page_b]


@pytest.mark.asyncio
async def test_trailing_slash_slug_normalizes_to_the_same_url():
    payload = json.loads(SAMPLE)
    canonical = f"https://www.ycombinator.com/companies/{payload['hits'][0]['slug']}"
    payload["hits"][0]["slug"] = payload["hits"][0]["slug"] + "/"
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))

    assert records[0].source_url == canonical


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_fetch_result_is_attached_to_every_record():
    fetch_result = _fetch_result(SAMPLE)
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))

    assert records
    for record in records:
        assert record.fetch_result is fetch_result
        assert record.fetch_result.content_hash == "hash"
        assert record.fetch_result.status_code == 200


@pytest.mark.asyncio
async def test_source_url_is_the_public_yc_company_page():
    adapter = _adapter(_StubHttpClient())
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    for record in records:
        assert record.source_url.startswith("https://www.ycombinator.com/companies/")


# ---------------------------------------------------------------------------
# Discovery / pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_partitions_by_batch_using_the_real_facet_listing():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=300, page_size=100)
    discovered = await _collect(adapter)

    batches = {d.metadata["batch"] for d in discovered}
    assert None not in batches
    assert len(batches) >= 2  # the target exceeds any single batch
    real_batches = set(json.loads(BATCH_FACETS)["facets"]["batch"])
    assert batches <= real_batches


@pytest.mark.asyncio
async def test_discovery_requests_no_more_than_the_target():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=250, page_size=100)
    discovered = await _collect(adapter)

    assert sum(d.metadata["hits_per_page"] for d in discovered) == 250


@pytest.mark.asyncio
async def test_pagination_splits_a_batch_into_multiple_pages():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=150, page_size=50)
    discovered = await _collect(adapter)

    pages = [d.metadata["page"] for d in discovered]
    assert pages[:3] == [0, 1, 2]
    assert all(d.metadata["hits_per_page"] <= 50 for d in discovered)


@pytest.mark.asyncio
async def test_partitioning_lets_the_target_exceed_the_1000_hit_query_cap():
    """This is the whole reason for batch partitioning: 1,000+ records
    without a code change."""
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=1500, page_size=100)
    discovered = await _collect(adapter)

    total = sum(d.metadata["hits_per_page"] for d in discovered)
    assert total == 1500
    assert total > MAX_HITS_PER_QUERY
    # No single partitioned query may ask beyond the per-query cap.
    for batch in {d.metadata["batch"] for d in discovered}:
        in_batch = [d for d in discovered if d.metadata["batch"] == batch]
        assert sum(d.metadata["hits_per_page"] for d in in_batch) <= MAX_HITS_PER_QUERY


@pytest.mark.asyncio
async def test_discovery_order_is_deterministic_across_runs():
    def run():
        client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
        return _adapter(client, max_results=400, page_size=100)

    first = [d.url async for d in run().discover()]
    second = [d.url async for d in run().discover()]
    assert first == second


@pytest.mark.asyncio
async def test_discovery_falls_back_to_unpartitioned_paging_when_facets_fail():
    adapter = _adapter(_FailingHttpClient(), max_results=200, page_size=100)
    discovered = await _collect(adapter)

    assert len(discovered) == 2
    assert all(d.metadata["batch"] is None for d in discovered)


@pytest.mark.asyncio
async def test_unpartitioned_fallback_never_exceeds_the_per_query_cap():
    adapter = _adapter(_FailingHttpClient(), max_results=5000, page_size=100)
    discovered = await _collect(adapter)

    assert sum(d.metadata["hits_per_page"] for d in discovered) == MAX_HITS_PER_QUERY


@pytest.mark.asyncio
async def test_facet_error_body_falls_back_rather_than_inventing_batches():
    client = _StubHttpClient(body=ERROR_BODY)
    adapter = _adapter(client, max_results=100, page_size=100)
    discovered = await _collect(adapter)

    assert all(d.metadata["batch"] is None for d in discovered)


@pytest.mark.asyncio
async def test_zero_target_discovers_nothing():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=0)
    assert await _collect(adapter) == []
    assert client.requested == []


@pytest.mark.asyncio
async def test_query_urls_carry_the_ai_tag_filter_and_public_credentials():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, max_results=100, page_size=100)
    discovered = await _collect(adapter)

    url = discovered[0].url
    assert "YCCompany_production" in url
    assert "x-algolia-api-key" in url
    assert "Artificial+Intelligence" in url or "Artificial%20Intelligence" in url


@pytest.mark.asyncio
async def test_credentials_are_overridable_without_code_change():
    client = _StubHttpClient(by_substring={"facets": BATCH_FACETS})
    adapter = _adapter(client, app_id="ABC123", api_key="rotated-key", max_results=10)
    discovered = await _collect(adapter)

    assert "abc123-dsn.algolia.net" in discovered[0].url
    assert "rotated-key" in discovered[0].url


@pytest.mark.asyncio
async def test_page_size_is_clamped_to_the_api_maximum():
    adapter = _adapter(_StubHttpClient(), page_size=99_999)
    assert adapter.page_size == 1000


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


def test_registry_entry_matches_the_adapter():
    from src.config.sources import SOURCE_REGISTRY, Vertical

    entry = next(s for s in SOURCE_REGISTRY if s.name == YCombinatorStartupsAdapter.name)
    assert entry.vertical == Vertical.STARTUPS
    assert entry.enabled
