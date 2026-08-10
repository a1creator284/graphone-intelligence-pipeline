from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from src.pipeline.research import run_research_pipeline
from src.storage.models import EntityMappingLog, ProcessingError, RawDocument, ResearchPaper  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
ARXIV_XML = (FIXTURES / "arxiv_sample_response.xml").read_text()
PWC_JSON = (FIXTURES / "papers_with_code_sample_response.json").read_text()

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
    pwc_body: str,
    github_response: Response | None = None,
):
    """Mocks all three real endpoints the pipeline calls. `github_response`
    defaults to a successful lookup so tests that don't care about GitHub
    still get a fully-mocked run (any unmocked call is a hard respx error,
    which is the behavior we want -- it catches accidental live calls)."""
    router.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(
        return_value=Response(200, text=arxiv_body)
    )
    router.get(url__regex=r"https://paperswithcode\.com/api/v1/papers/.*").mock(
        return_value=Response(200, text=pwc_body)
    )
    router.get(url__regex=r"https://api\.github\.com/repos/.*").mock(
        return_value=github_response or Response(200, json=DEFAULT_GITHUB_JSON)
    )


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_persists_valid_papers_from_both_sources(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON)

    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)

    # target=10 split across 2 adapters -> 5 each -> 1 page per adapter (page_size=50)
    assert result.discovered == 2  # 1 arxiv page + 1 pwc page
    # arxiv fixture: 2 valid + 1 with malformed paper_url; pwc fixture: 2 valid + 1 filtered by adapter (blank title)
    assert result.parsed == 5  # 3 from arxiv adapter (incl. malformed-url one) + 2 from pwc adapter
    assert result.valid_records == 4
    assert result.rejected == 1  # the arxiv entry with a non-URL paper identifier, caught by schema validation


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_enriches_github_stars_for_linked_repo(db_session):
    _mock_all_pages(
        respx,
        arxiv_body=ARXIV_XML,
        pwc_body=PWC_JSON,
        github_response=Response(200, json={**DEFAULT_GITHUB_JSON, "stargazers_count": 999}),
    )
    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    # Both fixtures link the same example-lab/sparse-attention repo -> both enriched
    assert result.github_enriched == 2

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert row.github_stars == 999
        assert row.github_stars >= 0  # never fabricated negative


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_deduplicates_same_paper_across_runs(db_session):
    """Running the pipeline twice against identical mocked responses must
    not create duplicate rows -- proves the ON CONFLICT DO NOTHING path is
    actually exercised end-to-end, not just at the repository unit level."""
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON)

    result1 = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    result2 = await run_research_pipeline(db_session, target=10, max_concurrency=4)

    assert result1.valid_records == 4
    assert result2.valid_records == 0
    assert result2.duplicates == 4

    from sqlalchemy import func, select

    count = (await db_session.execute(select(func.count()).select_from(ResearchPaper))).scalar_one()
    assert count == 4


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_records_provenance_raw_documents(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON)
    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import func, select

    count = (await db_session.execute(select(func.count()).select_from(RawDocument))).scalar_one()
    # One raw_documents row per fetched page: 1 arxiv page + 1 pwc page = 2
    assert count == 2


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_records_processing_errors_for_rejected_records(db_session):
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON)
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
        pwc_body=PWC_JSON,
        github_response=Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}, json={}),
    )

    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    assert result.github_enrichment_skipped_rate_limited is True

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    assert len(rows) == 2  # github_url kept as discovered
    for row in rows:
        assert row.github_stars is None  # never guessed under rate-limit


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_github_404_nulls_out_unverified_repo_link(db_session):
    """A repo link that doesn't resolve via the API must not be persisted
    as if it were confirmed."""
    _mock_all_pages(respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON, github_response=Response(404, json={"message": "Not Found"}))

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
    respx.get(url__regex=r"https://paperswithcode\.com/api/v1/papers/.*").mock(return_value=Response(200, text=PWC_JSON))
    respx.get(url__regex=r"https://api\.github\.com/repos/.*").mock(return_value=Response(200, json=DEFAULT_GITHUB_JSON))

    # Should not raise -- the arxiv page's ParsingError is caught by the
    # worker pool and logged, PWC papers still get processed.
    result = await run_research_pipeline(db_session, target=10, max_concurrency=4)
    assert result.valid_records == 2  # only the 2 valid PWC papers persist


@pytest.mark.asyncio
@respx.mock
async def test_pipeline_never_persists_negative_or_fabricated_stars(db_session):
    """Cross-check against the DB CheckConstraint: the enrichment client
    only ever forwards what the API returned, and GitHub's API never
    returns negative counts, so this documents the invariant end-to-end."""
    _mock_all_pages(
        respx, arxiv_body=ARXIV_XML, pwc_body=PWC_JSON,
        github_response=Response(200, json={**DEFAULT_GITHUB_JSON, "stargazers_count": 0}),
    )
    await run_research_pipeline(db_session, target=10, max_concurrency=4)

    from sqlalchemy import select

    rows = (await db_session.execute(select(ResearchPaper).where(ResearchPaper.github_url.isnot(None)))).scalars().all()
    for row in rows:
        assert row.github_stars is not None
        assert row.github_stars >= 0
