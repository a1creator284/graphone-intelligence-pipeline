"""
Startups pipeline tests (YC company directory -> validation -> storage).

Fixtures are the same REAL captured YC Algolia responses used by
tests/test_yc_startups_adapter.py (see that module for the exact live
queries and capture date).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response
from sqlalchemy import func, select

from src.pipeline.startups import run_startups_pipeline
from src.storage.models import EntityMappingLog, ProcessingError, RawDocument, Startup

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "yc_algolia_sample_response.json").read_text()
BATCH_FACETS = (FIXTURES / "yc_algolia_batch_facets.json").read_text()
ERROR_BODY = (FIXTURES / "yc_algolia_error_response.json").read_text()

ALGOLIA_RE = r"https://45bwzj1sgc-dsn\.algolia\.net/1/indexes/YCCompany_production.*"
SAMPLE_HIT_COUNT = len(json.loads(SAMPLE)["hits"])


def _mock_algolia(router: respx.MockRouter, *, page_body: str = SAMPLE, facet_body: str = BATCH_FACETS):
    """The facet query and the page queries hit the same path; distinguish
    them by the `facets=` parameter that only discovery sends."""

    def responder(request):
        if "facets" in str(request.url):
            return Response(200, text=facet_body)
        return Response(200, text=page_body)

    router.get(url__regex=ALGOLIA_RE).mock(side_effect=responder)


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_persists_real_startups(db_session):
    _mock_algolia(respx)

    result = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert result.valid_records == SAMPLE_HIT_COUNT
    assert result.rejected == 0
    assert result.by_source == {"ycombinator_directory": SAMPLE_HIT_COUNT}

    rows = (await db_session.execute(select(Startup))).scalars().all()
    expected = {h["name"] for h in json.loads(SAMPLE)["hits"]}
    assert {r.entity_name for r in rows} == expected


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_writes_provenance_for_every_startup(db_session):
    _mock_algolia(respx)

    await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    rows = (await db_session.execute(select(Startup))).scalars().all()
    assert rows
    for row in rows:
        assert row.raw_document_id is not None
        assert row.source_name == "ycombinator_directory"
        assert row.source_url.startswith("https://www.ycombinator.com/companies/")

    raw_count = (await db_session.execute(select(func.count()).select_from(RawDocument))).scalar_one()
    assert raw_count >= 1


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_deduplicates_across_runs(db_session):
    _mock_algolia(respx)

    first = await run_startups_pipeline(db_session, target=5, max_concurrency=4)
    second = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert first.valid_records == SAMPLE_HIT_COUNT
    assert second.valid_records == 0
    assert second.duplicates == SAMPLE_HIT_COUNT

    total = (await db_session.execute(select(func.count()).select_from(Startup))).scalar_one()
    assert total == SAMPLE_HIT_COUNT


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_resolves_companies_onto_canonical_entities(db_session):
    _mock_algolia(respx)

    result = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert result.entities_resolved == SAMPLE_HIT_COUNT
    rows = (await db_session.execute(select(Startup))).scalars().all()
    assert all(r.canonical_entity_id is not None for r in rows)

    logged = (await db_session.execute(select(func.count()).select_from(EntityMappingLog))).scalar_one()
    assert logged == SAMPLE_HIT_COUNT


@pytest.mark.asyncio
@respx.mock
async def test_missing_team_size_is_persisted_as_null_not_zero(db_session):
    """Derived from the real response by nulling `team_size` on one hit."""
    payload = json.loads(SAMPLE)
    payload["hits"][0]["team_size"] = None
    target_name = payload["hits"][0]["name"]
    _mock_algolia(respx, page_body=json.dumps(payload))

    await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    row = (
        await db_session.execute(select(Startup).where(Startup.entity_name == target_name))
    ).scalar_one()
    assert row.employee_count is None


@pytest.mark.asyncio
@respx.mock
async def test_source_error_body_produces_zero_records_not_fake_ones(db_session):
    _mock_algolia(respx, page_body=ERROR_BODY, facet_body=ERROR_BODY)

    result = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert result.valid_records == 0
    total = (await db_session.execute(select(func.count()).select_from(Startup))).scalar_one()
    assert total == 0


@pytest.mark.asyncio
@respx.mock
async def test_target_shortfall_is_reported_honestly_not_padded(db_session):
    """The fixture holds 5 real companies; asking for 50 must yield 5."""
    _mock_algolia(respx)

    result = await run_startups_pipeline(db_session, target=50, max_concurrency=4)

    assert result.target == 50
    assert result.valid_records == SAMPLE_HIT_COUNT
    assert result.valid_records < result.target

    total = (await db_session.execute(select(func.count()).select_from(Startup))).scalar_one()
    assert total == SAMPLE_HIT_COUNT


@pytest.mark.asyncio
@respx.mock
async def test_invalid_record_is_rejected_and_logged_not_repaired(db_session):
    """A hit with a blank name has no recoverable identity; it must be
    dropped, and it must not appear in the startups table."""
    payload = json.loads(SAMPLE)
    payload["hits"][0]["name"] = "   "
    _mock_algolia(respx, page_body=json.dumps(payload))

    result = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert result.valid_records == SAMPLE_HIT_COUNT - 1
    total = (await db_session.execute(select(func.count()).select_from(Startup))).scalar_one()
    assert total == SAMPLE_HIT_COUNT - 1


@pytest.mark.asyncio
@respx.mock
async def test_malformed_page_does_not_crash_the_run(db_session):
    _mock_algolia(respx, page_body="{not json")

    result = await run_startups_pipeline(db_session, target=5, max_concurrency=4)

    assert result.valid_records == 0
    total = (await db_session.execute(select(func.count()).select_from(Startup))).scalar_one()
    assert total == 0


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_paginates_beyond_a_single_page(db_session):
    """target > page_size must issue multiple page requests, proving real
    pagination rather than one fixed fetch."""
    _mock_algolia(respx)

    result = await run_startups_pipeline(db_session, target=6, max_concurrency=4, page_size=2)

    # 6 requested at 2/page -> 3 discovered pages (plus the facet query,
    # which is not counted as a discovered item).
    assert result.discovered == 3
