from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from src.pipeline.research import run_research_pipeline
from src.storage.models import EntityMappingLog, ProcessingError, RawDocument, ResearchPaper  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
ARXIV_XML = (FIXTURES / "arxiv_sample_response.xml").read_text()
# Papers With Code is dead upstream (302 -> HTML) and has been replaced by
# OpenAlex in ADAPTER_CLASSES; this fixture is a real captured OpenAlex
# response (see tests/test_openalex_adapter.py for the exact query).
OPENALEX_JSON = (FIXTURES / "openalex_sample_response.json").read_text()

DEFAULT_GITHUB_JSON = {
    "owner": {"login": "example-lab"},
    "name": "sparse-attention",
    "stargazers_count": 314,
    "forks_count": 10,
    "updated_at": "2026-08-01T00:00:00Z",
}


def _mock_all_pages(
    router: respx.MockRouter,
    *,
    arxiv_body: str,
    openalex_body: str,
    github_response: Response | None = None,
):
    """Mocks all three real endpoints the pipeline calls. `github_response`
    defaults to a successful lookup so tests that don't care about GitHub
    still get a fully-mocked run (any unmocked call is a hard respx error,
    which is the behavior we want -- it catches accidental live calls)."""
    router.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(
        return_value=Response(200, text=arxiv_body)
    )
    router.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=Response(200, text=openalex_body)
    )
    router.get(url__regex=r"https://api\.github\.com/repos/.*").mock(
        return_value=github_response or Response(200, json=DEFAULT_GITHUB_JSON)
    )


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_persists_valid_papers_from_both_sources(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON)

    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)

    # target=10, fill-forward: arxiv asked for 5 -> 1 page; it returns 3, so
    # openalex is asked for the outstanding 7 -> 1 page (page_size=50).
    assert result.discovered == 2  # 1 arxiv page + 1 openalex page
    # arxiv fixture: 2 valid + 1 with malformed paper_url; openalex fixture: 3 valid
    assert result.parsed == 6
    assert result.valid_records == 5
    assert result.rejected == 1  # the arxiv entry with a non-URL paper identifier, caught by schema validation
    assert result.by_source == {"arxiv": 3, "openalex": 3}


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_enriches_github_stars_for_linked_repo(db_session):
    _mock_all_pages(
        respx,
        arxiv_body=ARXIV_XML,
        openalex_body=OPENALEX_JSON,
        github_response=Response(200, json={**DEFAULT_GITHUB_JSON, "stargazers_count": 999}),
    )
    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    # Only the arxiv fixture carries an explicit repo link; OpenAlex exposes
    # no repository relation at all, so it contributes zero github_urls.
    assert result.github_enriched == 1

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    assert len(rows) == 1
    for row in rows:
        assert row.github_stars == 999
        assert row.github_stars >= 0  # never fabricated negative


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_deduplicates_same_paper_across_runs(db_session):
    """Running the pipeline twice against identical mocked responses must
    not create duplicate rows -- proves the ON CONFLICT DO NOTHING path is
    actually exercised end-to-end, not just at the repository unit level."""
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON)

    result1 = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    result2 = await run_research_pipeline(db_session, target=10, max_concurrency=4)

    assert result1.valid_records == 5
    assert result2.valid_records == 0
    assert result2.duplicates == 5

    from sqlalchemy import func, select

    count = (await db_session.execute(select(func.count()).select_from(ResearchPaper))).scalar_one()
    assert count == 5


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_records_provenance_raw_documents(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON)
    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import func, select

    count = (await db_session.execute(select(func.count()).select_from(RawDocument))).scalar_one()
    # One raw_documents row per fetched page: 1 arxiv page + 1 openalex page = 2
    assert count == 2


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_records_processing_errors_for_rejected_records(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON)
    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import select

    rows = (
        await db_session.execute(select(ProcessingError).where(ProcessingError.error_category == "ValidationError"))
    ).scalars().all()
    assert len(rows) == 1  # the arxiv entry with a non-URL paper identifier


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_github_rate_limit_leaves_stars_null_not_fabricated(db_session):
    _mock_all_pages(
        respx,
        arxiv_body=ARXIV_XML,
        openalex_body=OPENALEX_JSON,
        github_response=Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}, json={}),
    )

    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    assert result.github_enrichment_skipped_rate_limited is True

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    assert len(rows) == 1  # github_url kept as discovered
    for row in rows:
        assert row.github_stars is None  # never guessed under rate-limit


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_github_404_nulls_out_unverified_repo_link(db_session):
    """A repo link that doesn't resolve via the API must not be persisted
    as if it were confirmed."""
    _mock_all_pages(
        respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON, github_response=Response(404, json={"message": "Not Found"})
    )

    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper))).scalars().all()
    for row in rows:
        assert row.github_url is None
        assert row.github_stars is None


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_malformed_arxiv_response_does_not_crash_run(db_session):
    respx.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(return_value=Response(200, text="<broken"))
    respx.get(url__regex=r"https://api\.openalex\.org/works.*").mock(return_value=Response(200, text=OPENALEX_JSON))
    respx.get(url__regex=r"https://api\.github\.com/repos/.*").mock(return_value=Response(200, json=DEFAULT_GITHUB_JSON))

    # Should not raise -- the arxiv page's ParsingError is caught by the
    # worker pool and logged, OpenAlex papers still get processed.
    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    assert result.valid_records == 3  # only the 3 valid OpenAlex papers persist


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_never_persists_negative_or_fabricated_stars(db_session):
    """Cross-check against the DB CheckConstraint: the enrichment client
    only ever forwards what the API returned, and GitHub's API never
    returns negative counts, so this documents the invariant end-to-end."""
    _mock_all_pages(
        respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON,
        github_response=Response(200, json={**DEFAULT_GITHUB_JSON, "stargazers_count": 0}),
    )
    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    for row in rows:
        assert row.github_stars is not None
        assert row.github_stars >= 0


# ---------------------------------------------------------------------------
# Fill-forward target allocation
#
# The previous allocation was a flat `target // len(ADAPTER_CLASSES)` split,
# which silently under-delivered whenever one source ran dry. These tests pin
# the replacement behaviour -- and, just as importantly, that it still never
# invents a record to close a gap.
# ---------------------------------------------------------------------------

EMPTY_ARXIV_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
)
EMPTY_OPENALEX_JSON = '{"meta": {"count": 0, "page": 1, "per_page": 50}, "results": []}'


def _arxiv_request_urls() -> list[str]:
    return [str(call.request.url) for call in respx.calls if "export.arxiv.org" in str(call.request.url)]


def _openalex_request_urls() -> list[str]:
    return [str(call.request.url) for call in respx.calls if "api.openalex.org" in str(call.request.url)]


@pytest.mark.asyncio
@respx.mock
async def test_shortfall_from_first_adapter_is_carried_forward_to_the_next(db_session):
    """arXiv returns nothing, so OpenAlex must be asked for the FULL target
    (10), not the naive half-split (5)."""
    _mock_all_pages(respx, arxiv_body=EMPTY_ARXIV_XML, openalex_body=OPENALEX_JSON)

    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)

    openalex_urls = _openalex_request_urls()
    assert len(openalex_urls) == 1
    assert "per-page=10" in openalex_urls[0]
    assert result.by_source.get("arxiv", 0) == 0
    assert result.by_source["openalex"] == 3


@pytest.mark.asyncio
@respx.mock
async def test_second_pass_retries_a_non_exhausted_adapter_when_target_missed(db_session):
    """target=6 -> arXiv asked for 3 and delivers 3 (not exhausted);
    OpenAlex is dead this run and delivers 0. The pipeline makes one extra
    attempt against arXiv with a raised ceiling rather than accepting the
    shortfall without trying."""
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=EMPTY_OPENALEX_JSON)

    result = await run_research_pipeline(db_session, target=6, max_concurrency=4)

    arxiv_urls = _arxiv_request_urls()
    assert len(arxiv_urls) == 2, arxiv_urls
    assert "max_results=3" in arxiv_urls[0]
    assert "max_results=6" in arxiv_urls[1]  # 3 already requested + 3 shortfall

    # The retry re-fetches the same 3 papers; they are deduped by URL, so the
    # count does NOT inflate. Under-delivery is reported honestly.
    assert result.by_source["arxiv"] == 3
    assert result.valid_records == 2  # 3 parsed, 1 rejected by schema validation
    assert result.rejected == 1


@pytest.mark.asyncio
@respx.mock
async def test_fill_forward_never_fabricates_records_when_all_sources_dry(db_session):
    """Both sources empty -> zero records. The target is never padded."""
    _mock_all_pages(respx, arxiv_body=EMPTY_ARXIV_XML, openalex_body=EMPTY_OPENALEX_JSON)

    result = await run_research_pipeline(db_session, target=50, max_concurrency=4)

    assert result.valid_records == 0
    assert result.parsed == 0
    assert result.rejected == 0

    from sqlalchemy import func, select

    count = (await db_session.execute(select(func.count()).select_from(ResearchPaper))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_extra_requests_once_target_is_already_met(db_session):
    """target=2 is satisfied by arXiv alone, so OpenAlex is never called --
    fill-forward must not over-collect."""
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, openalex_body=OPENALEX_JSON)

    await run_research_pipeline(db_session, target=2, max_concurrency=4)

    assert len(_arxiv_request_urls()) == 1
    assert _openalex_request_urls() == []
