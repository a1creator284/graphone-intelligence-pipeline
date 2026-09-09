"""
OpenAlex adapter tests.

Both fixtures are REAL, unmodified responses captured live from
api.openalex.org on 2026-09-09:

  openalex_sample_response.json
    curl "https://api.openalex.org/works?filter=concepts.id:C154945302
          &sort=publication_date:desc&per-page=3&page=1
          &select=id,doi,title,display_name,publication_date,authorships,
                  primary_location,ids&mailto=..."

  openalex_sample_response_no_doi.json
    same, plus the `has_doi:false` filter -- captured specifically to get
    real records whose optional `doi` field is genuinely null.

Where a test needs a shape the live API did not hand us (a work with no
publication_date, an error body), it *derives* it from the captured
response by deleting a key, and says so. No invented paper metadata.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crawlers.base import DiscoveredUrl
from src.crawlers.http import FetchResult
from src.crawlers.openalex import MAX_BASIC_PAGING_RESULTS, OpenAlexAdapter
from src.errors import ParsingError

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "openalex_sample_response.json").read_text()
SAMPLE_NO_DOI = (FIXTURES / "openalex_sample_response_no_doi.json").read_text()


def _fetch_result(body: str) -> FetchResult:
    return FetchResult(
        url="https://api.openalex.org/works?filter=concepts.id:C154945302",
        status_code=200,
        text=body,
        content_hash="hash",
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def adapter() -> OpenAlexAdapter:
    return OpenAlexAdapter(http_client=None)


# ---- 1. successful response parsing -------------------------------------


@pytest.mark.asyncio
async def test_parse_returns_one_record_per_result(adapter):
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    assert len(records) == 3
    assert {r.record_type for r in records} == {"research_paper"}
    assert {r.source_name for r in records} == {"openalex"}


@pytest.mark.asyncio
async def test_parse_maps_required_fields_verbatim(adapter):
    """Every asserted value is copied from the captured response."""
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    first = records[0].data
    assert first["title"] == "Artificial Intelligence in Plant Sciences"
    assert first["authors"] == ["Dr. Nupur Prasad"]
    assert first["paper_url"] == "https://doi.org/10.5281/zenodo.17036033"
    assert first["paper_external_id"] == "W7166029900"
    assert first["published_date"] == datetime(2045, 12, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_parse_extracts_all_authors_in_order(adapter):
    records = await adapter.parse(_fetch_result(SAMPLE), DiscoveredUrl(url="x"))
    assert records[2].data["authors"] == ["Greca, Pia", "Harrington, Jonathan"]


@pytest.mark.asyncio
async def test_parse_never_populates_github_fields(adapter):
    """OpenAlex exposes no repository relation, so these stay NULL --
    never inferred from a similar name (Section 10)."""
    for body in (SAMPLE, SAMPLE_NO_DOI):
        records = await adapter.parse(_fetch_result(body), DiscoveredUrl(url="x"))
        assert records, "fixture should yield records"
        for record in records:
            assert record.data["github_url"] is None
            assert record.data["github_stars"] is None


@pytest.mark.asyncio
async def test_parsed_records_pass_the_research_schema(adapter):
    """The whole point of the adapter is to feed the pipeline, so the
    captured real records must validate without repair."""
    from src.validation.schemas import validate_research_paper

    records = await adapter.parse(_fetch_result(SAMPLE_NO_DOI), DiscoveredUrl(url="x"))
    for record in records:
        data = dict(record.data)
        data["source_name"] = record.source_name
        validated, error = validate_research_paper(data)
        assert validated is not None, error


# ---- 2/3. missing optional fields ---------------------------------------


@pytest.mark.asyncio
async def test_records_with_null_doi_fall_back_to_landing_page(adapter):
    """Real records from the has_doi:false capture: `doi` is genuinely
    null, so the URL must come from primary_location.landing_page_url --
    a value present in the response, not a constructed one."""
    records = await adapter.parse(_fetch_result(SAMPLE_NO_DOI), DiscoveredUrl(url="x"))
    assert len(records) == 3
    assert records[1].data["paper_url"] == (
        "https://research-information.bris.ac.uk/en/publications/a6cf1a27-c66d-49dd-bfc7-0d6dac03ec95"
    )
    assert records[2].data["paper_url"] == "https://eprints.lancs.ac.uk/id/eprint/233063/"


@pytest.mark.asyncio
async def test_missing_publication_date_yields_none_not_a_guess(adapter):
    """Derived from the captured response by deleting `publication_date`
    from the first result -- the adapter must leave the date NULL."""
    payload = json.loads(SAMPLE)
    del payload["results"][0]["publication_date"]

    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert records[0].data["published_date"] is None
    # ...and the other records are unaffected
    assert records[1].data["published_date"] == datetime(2045, 12, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_missing_authorships_yields_empty_list(adapter):
    """Derived by deleting `authorships` from a real result."""
    payload = json.loads(SAMPLE)
    del payload["results"][0]["authorships"]

    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert records[0].data["authors"] == []


@pytest.mark.asyncio
async def test_work_without_any_usable_url_is_skipped(adapter):
    """Derived by stripping every URL-bearing field from a real result.
    The record is dropped, not given a synthesized URL."""
    payload = json.loads(SAMPLE)
    payload["results"][0]["doi"] = None
    payload["results"][0]["primary_location"] = None
    payload["results"][0]["id"] = None

    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert len(records) == 2  # 3 results, 1 skipped


@pytest.mark.asyncio
async def test_work_without_title_is_skipped(adapter):
    """Derived by nulling `title`/`display_name` on a real result."""
    payload = json.loads(SAMPLE)
    payload["results"][0]["title"] = None
    payload["results"][0]["display_name"] = None

    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert len(records) == 2
    assert all(r.data["title"] for r in records)


@pytest.mark.asyncio
async def test_empty_results_list_yields_no_records(adapter):
    """OpenAlex returns HTTP 200 with `results: []` for a query that
    matches nothing (verified live). That is zero records, not an error."""
    payload = json.loads(SAMPLE)
    payload["results"] = []
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert records == []


# ---- 4. invalid / malformed responses -----------------------------------


@pytest.mark.asyncio
async def test_malformed_json_raises_parsing_error(adapter):
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result("{not valid json"), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_html_error_page_raises_parsing_error_not_silently_ignored(adapter):
    """The exact failure mode that killed Papers With Code: a redirect to
    an HTML page. It must fail loudly and produce zero records."""
    with pytest.raises(ParsingError):
        await adapter.parse(
            _fetch_result("<!DOCTYPE html><html><body>Moved</body></html>"), DiscoveredUrl(url="x")
        )


@pytest.mark.asyncio
async def test_openalex_error_body_raises_parsing_error(adapter):
    """Real OpenAlex 400 body shape (captured live from
    /works?filter=badfilter:1) -- valid JSON but no `results` key."""
    body = json.dumps(
        {"error": "Invalid query parameters error.", "message": "badfilter is not a valid field."}
    )
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result(body), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_results_not_a_list_raises_parsing_error(adapter):
    body = json.dumps({"meta": {}, "results": {"unexpected": "shape"}})
    with pytest.raises(ParsingError):
        await adapter.parse(_fetch_result(body), DiscoveredUrl(url="x"))


@pytest.mark.asyncio
async def test_non_dict_entries_in_results_are_skipped(adapter):
    payload = json.loads(SAMPLE)
    payload["results"].append("not-an-object")
    records = await adapter.parse(_fetch_result(json.dumps(payload)), DiscoveredUrl(url="x"))
    assert len(records) == 3


# ---- 5. pagination ------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_paginates_by_page_size():
    adapter = OpenAlexAdapter(http_client=None, max_results=120, page_size=50)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 3
    assert [d.metadata["page"] for d in urls] == [1, 2, 3]
    # last page only asks for the remainder, never more than requested
    assert [d.metadata["per_page"] for d in urls] == [50, 50, 20]
    assert "per-page=50&page=1" in urls[0].url
    assert "per-page=20&page=3" in urls[2].url


@pytest.mark.asyncio
async def test_discover_single_page_when_target_below_page_size():
    adapter = OpenAlexAdapter(http_client=None, max_results=5, page_size=50)
    urls = [d async for d in adapter.discover()]
    assert len(urls) == 1
    assert "per-page=5" in urls[0].url


@pytest.mark.asyncio
async def test_discover_respects_basic_paging_cap():
    """OpenAlex caps page*per-page at 10,000; we stop instead of issuing
    requests that would 400."""
    adapter = OpenAlexAdapter(http_client=None, max_results=25_000, page_size=200)
    urls = [d async for d in adapter.discover()]
    assert sum(d.metadata["per_page"] for d in urls) == MAX_BASIC_PAGING_RESULTS


@pytest.mark.asyncio
async def test_page_size_clamped_to_api_maximum():
    adapter = OpenAlexAdapter(http_client=None, max_results=1000, page_size=5000)
    urls = [d async for d in adapter.discover()]
    assert urls[0].metadata["per_page"] == 200


# ---- 6. source URL / provenance -----------------------------------------


@pytest.mark.asyncio
async def test_source_url_matches_paper_url_and_carries_fetch_result(adapter):
    fetch_result = _fetch_result(SAMPLE)
    records = await adapter.parse(fetch_result, DiscoveredUrl(url="x"))
    for record in records:
        assert record.source_url == record.data["paper_url"]
        assert record.fetch_result is fetch_result


@pytest.mark.asyncio
async def test_polite_pool_mailto_included_only_when_configured():
    without = OpenAlexAdapter(http_client=None, max_results=1)
    urls = [d async for d in without.discover()]
    assert "mailto=" not in urls[0].url

    with_mail = OpenAlexAdapter(http_client=None, max_results=1, mailto="pipeline@example.com")
    urls = [d async for d in with_mail.discover()]
    assert "mailto=pipeline@example.com" in urls[0].url


@pytest.mark.asyncio
async def test_discover_targets_the_official_api_and_ai_concept():
    adapter = OpenAlexAdapter(http_client=None, max_results=1)
    urls = [d async for d in adapter.discover()]
    assert urls[0].url.startswith("https://api.openalex.org/works?")
    assert "filter=concepts.id:C154945302" in urls[0].url


@pytest.mark.asyncio
async def test_adapter_identity_matches_source_registry():
    from src.config.sources import SOURCE_REGISTRY

    entry = next(s for s in SOURCE_REGISTRY if s.name == "openalex")
    assert entry.enabled is True
    assert entry.vertical == "research"
    assert OpenAlexAdapter.name == entry.name
    assert OpenAlexAdapter.vertical == "research"


@pytest.mark.asyncio
async def test_papers_with_code_is_disabled_but_retained():
    """The dead source stays documented in the registry; it must not be
    handed out as a usable research source."""
    from src.config.sources import SOURCE_REGISTRY, Vertical, get_sources_for_vertical

    pwc = next(s for s in SOURCE_REGISTRY if s.name == "papers_with_code")
    assert pwc.enabled is False
    active = {s.name for s in get_sources_for_vertical(Vertical.RESEARCH)}
    assert "papers_with_code" not in active
    assert {"arxiv", "openalex"} <= active
