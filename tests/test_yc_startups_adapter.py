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

    # `hits_per_page` is deliberately uniform (Algolia's offset is
    # page * hitsPerPage), so the target ceiling is carried by `max_records`,
    # which trims the final page at parse time.
    assert sum(d.metadata["max_records"] for d in discovered) == 250


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

    total = sum(d.metadata["max_records"] for d in discovered)
    assert total == 1500
    assert total > MAX_HITS_PER_QUERY
    # No single partitioned query may reach beyond the per-query cap. With a
    # uniform page size the deepest window is page * page_size + page_size.
    for batch in {d.metadata["batch"] for d in discovered}:
        in_batch = [d for d in discovered if d.metadata["batch"] == batch]
        deepest = max(
            d.metadata["page"] * d.metadata["hits_per_page"] + d.metadata["hits_per_page"]
            for d in in_batch
        )
        assert deepest <= MAX_HITS_PER_QUERY


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


# ---------------------------------------------------------------------------
# Regression: pagination offset must not re-serve companies as "duplicates"
#
# Algolia derives a query's offset from `page * hitsPerPage`. Discovery used
# to SHRINK hitsPerPage on a partition's final page to avoid overshooting the
# target, which slid the offset backwards and re-served hits already returned
# by the previous page. Live example (2026-09-09): batch "Summer 2024" has 162
# tagged hits, so discovery asked for page 0 @ hitsPerPage=100 (offset 0) then
# page 1 @ hitsPerPage=62 (offset 62) -- re-reading hits 62-99. Those 38
# re-reads collapsed on the (source_name, source_url) unique key and were
# counted as duplicates. Summed over the six multi-page batches in a 1,000
# record run that is 200 phantom duplicates, which is why a 1,000-record
# target persisted only ~800 rows.
#
# The companies were always DISTINCT and REAL -- entity resolution was never
# at fault, and the fix does not touch dedup identity or any threshold.
# ---------------------------------------------------------------------------


class _OffsetAccurateAlgoliaStub:
    """Serves a synthetic index the way Algolia really pages it.

    The hit *shape* comes from the captured live fixture; only the slug/name
    identity is generated so a batch large enough to need several pages can be
    simulated deterministically. Crucially, `offset = page * hitsPerPage`,
    which is the real API behaviour that exposed the bug.
    """

    def __init__(self, *, batch: str, total: int, facets: str):
        self.batch = batch
        self.total = total
        self.facets = facets
        self.requested: list[str] = []
        template = json.loads(SAMPLE)["hits"][0]
        self._index = []
        for i in range(total):
            hit = dict(template)
            hit["slug"] = f"company-{i:04d}"
            hit["name"] = f"Company {i:04d}"
            self._index.append(hit)

    @staticmethod
    def _param(url: str, key: str) -> int:
        from urllib.parse import parse_qs, urlparse

        return int(parse_qs(urlparse(url).query)[key][0])

    async def get(self, url: str, *, headers=None) -> FetchResult:
        self.requested.append(url)
        if "facets" in url:
            return _fetch_result(self.facets, url=url)
        hits_per_page = self._param(url, "hitsPerPage")
        page = self._param(url, "page")
        offset = page * hits_per_page  # the real Algolia semantics
        window = self._index[offset : offset + hits_per_page]
        return _fetch_result(
            json.dumps({"hits": window, "nbHits": self.total, "page": page}), url=url
        )


async def _collect_records(adapter) -> list:
    records = []
    async for discovered in adapter.discover():
        fetch_result = await adapter.fetch(discovered)
        records.extend(await adapter.parse(fetch_result, discovered))
    return records


@pytest.mark.asyncio
async def test_multi_page_batch_returns_distinct_companies_not_duplicates():
    """The exact live failure: a 162-hit batch paged at 100 must yield 162
    DISTINCT companies, not 100 + 62 overlapping ones."""
    client = _OffsetAccurateAlgoliaStub(
        batch="Summer 2024",
        total=162,
        facets=json.dumps({"facets": {"batch": {"Summer 2024": 162}}}),
    )
    adapter = _adapter(client, max_results=162, page_size=100)

    records = await _collect_records(adapter)
    urls = [r.source_url for r in records]

    assert len(urls) == 162
    assert len(set(urls)) == 162, "distinct YC companies were collapsed as duplicates"


@pytest.mark.asyncio
async def test_hits_per_page_is_uniform_within_a_partition():
    """Root cause guard: a shrinking hitsPerPage moves Algolia's offset
    backwards, so every page of a partition must request the same size."""
    client = _OffsetAccurateAlgoliaStub(
        batch="Summer 2024",
        total=162,
        facets=json.dumps({"facets": {"batch": {"Summer 2024": 162}}}),
    )
    adapter = _adapter(client, max_results=162, page_size=100)

    discovered = await _collect(adapter)
    per_batch: dict[str, set[int]] = {}
    for d in discovered:
        per_batch.setdefault(d.metadata["batch"], set()).add(d.metadata["hits_per_page"])
    for batch, sizes in per_batch.items():
        assert len(sizes) == 1, f"{batch} paged with mixed hitsPerPage {sizes}"

    # Offsets must strictly advance by the page size -- never overlap.
    offsets = [d.metadata["page"] * d.metadata["hits_per_page"] for d in discovered]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)


@pytest.mark.asyncio
async def test_target_is_still_a_ceiling_and_is_never_overshot():
    """The fix must not turn the target into an over-delivery: the trim now
    happens at parse time via `max_records`."""
    client = _OffsetAccurateAlgoliaStub(
        batch="Summer 2024",
        total=162,
        facets=json.dumps({"facets": {"batch": {"Summer 2024": 162}}}),
    )
    adapter = _adapter(client, max_results=120, page_size=100)

    records = await _collect_records(adapter)
    urls = [r.source_url for r in records]

    assert len(urls) == 120
    assert len(set(urls)) == 120


@pytest.mark.asyncio
async def test_genuine_repeat_of_the_same_company_is_still_deduplicated():
    """Legitimate duplicate protection is preserved: the same slug served
    twice in one page collapses to a single record."""
    payload = json.loads(SAMPLE)
    repeated = dict(payload["hits"][0])
    payload["hits"] = [repeated, dict(repeated), payload["hits"][1]]

    adapter = _adapter(_StubHttpClient())
    discovered = DiscoveredUrl(
        url="https://45bwzj1sgc-dsn.algolia.net/1/indexes/YCCompany_production",
        metadata={"page": 0, "hits_per_page": 100, "batch": None, "max_records": 100},
    )
    records = await adapter.parse(_fetch_result(json.dumps(payload)), discovered)

    urls = [r.source_url for r in records]
    assert len(urls) == 2
    assert len(set(urls)) == 2


@pytest.mark.asyncio
async def test_full_multi_batch_run_yields_the_full_distinct_target():
    """End-to-end shape of the live 1,000-record run: several multi-page
    batches must produce `target` distinct companies, with zero duplicates."""

    class _MultiBatchStub(_OffsetAccurateAlgoliaStub):
        def __init__(self, counts: dict[str, int]):
            super().__init__(
                batch="", total=0, facets=json.dumps({"facets": {"batch": counts}})
            )
            self.counts = counts
            self._per_batch = {}
            template = json.loads(SAMPLE)["hits"][0]
            for name, count in counts.items():
                slug_stem = name.lower().replace(" ", "-")
                hits = []
                for i in range(count):
                    hit = dict(template)
                    hit["slug"] = f"{slug_stem}-{i:04d}"
                    hit["name"] = f"{name} Company {i:04d}"
                    hits.append(hit)
                self._per_batch[name] = hits

        async def get(self, url: str, *, headers=None) -> FetchResult:
            self.requested.append(url)
            if "facets" in url:
                return _fetch_result(self.facets, url=url)
            batch = next(
                (b for b in self.counts if b.replace(" ", "+") in url), None
            )
            assert batch is not None, f"no batch filter in {url}"
            hits_per_page = self._param(url, "hitsPerPage")
            page = self._param(url, "page")
            offset = page * hits_per_page
            window = self._per_batch[batch][offset : offset + hits_per_page]
            return _fetch_result(json.dumps({"hits": window}), url=url)

    # Real batch sizes from the live facet listing that needed >1 page.
    client = _MultiBatchStub(
        {
            "Summer 2024": 162,
            "Summer 2026": 162,
            "Winter 2024": 159,
            "Summer 2023": 133,
            "Winter 2023": 133,
            "Winter 2022": 117,
            "Summer 2022": 96,
            "Summer 2025": 95,
        }
    )
    adapter = _adapter(client, max_results=1000, page_size=100)

    records = await _collect_records(adapter)
    urls = [r.source_url for r in records]

    assert len(urls) == 1000
    assert len(set(urls)) == 1000, (
        f"{len(urls) - len(set(urls))} distinct companies collapsed as duplicates"
    )
