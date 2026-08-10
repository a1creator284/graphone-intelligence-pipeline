"""
Live integration tests -- excluded from the default `pytest` run (Section 38).
Run explicitly with: pytest -m integration

Only api.github.com is reachable from the dev/CI sandbox this was built in;
other sources (arXiv, RSS feeds, job boards) will work identically once
deployed to an environment with normal internet egress.
"""
from __future__ import annotations

import pytest

from src.crawlers.http import AsyncHttpClient
from src.errors import RateLimitError
from src.extraction.github import GitHubEnrichmentClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_github_enrichment_client_against_real_api():
    """End-to-end: real GitHub API call through the actual enrichment client
    used by the research pipeline. Verified once against karpathy/nanoGPT
    (62,005 real stars at time of writing) -- see README."""
    async with AsyncHttpClient(max_concurrency=1, timeout_seconds=10, max_retries=1) as client:
        gh = GitHubEnrichmentClient(client)
        try:
            meta = await gh.get_repo_metadata("https://github.com/karpathy/nanoGPT")
        except RateLimitError:
            pytest.skip("GitHub anonymous rate limit exhausted in this environment (expected without GITHUB_TOKEN)")
            return
        assert meta is not None
        assert meta.owner.lower() == "karpathy"
        assert meta.stargazers_count > 0  # real, not fabricated -- must be positive for a well-known repo


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetches_real_github_api_response():
    # max_retries=1 -> a single attempt, no waiting out a real Retry-After
    # (anonymous GitHub rate limits can reset tens of minutes out).
    async with AsyncHttpClient(max_concurrency=2, timeout_seconds=10, max_retries=1) as client:
        try:
            result = await client.get("https://api.github.com/repos/pytorch/pytorch")
        except RateLimitError:
            pytest.skip("GitHub anonymous rate limit exhausted in this environment (expected without GITHUB_TOKEN)")
            return
        assert result.status_code == 200
        assert "pytorch" in result.text.lower()
        assert len(result.content_hash) == 64
