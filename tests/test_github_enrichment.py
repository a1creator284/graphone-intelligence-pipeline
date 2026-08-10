from __future__ import annotations

import httpx
import pytest

from src.crawlers.http import AsyncHttpClient
from src.errors import RateLimitError
from src.extraction.github import GitHubEnrichmentClient, parse_github_repo_url


# --- URL parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/pytorch/pytorch", ("pytorch", "pytorch")),
        ("https://github.com/pytorch/pytorch.git", ("pytorch", "pytorch")),
        ("https://github.com/pytorch/pytorch/", ("pytorch", "pytorch")),
        ("http://github.com/foo-bar/baz_qux", ("foo-bar", "baz_qux")),
    ],
)
def test_parse_valid_github_urls(url, expected):
    assert parse_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/foo/bar",
        "not a url",
        "",
        None,
        "https://github.com/just-an-org",  # no repo segment
    ],
)
def test_parse_invalid_github_urls_returns_none(url):
    assert parse_github_repo_url(url) is None


# --- enrichment client (mocked transport) ---------------------------------


def _client_with_mock(handler) -> AsyncHttpClient:
    http_client = AsyncHttpClient(max_concurrency=2, timeout_seconds=5, max_retries=1)
    http_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http_client


@pytest.mark.asyncio
async def test_get_repo_metadata_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "owner": {"login": "openai"},
                "name": "gpt-2",
                "stargazers_count": 21000,
                "forks_count": 5000,
                "updated_at": "2026-08-01T00:00:00Z",
            },
        )

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    meta = await gh.get_repo_metadata("https://github.com/openai/gpt-2")
    assert meta is not None
    assert meta.stargazers_count == 21000
    assert meta.owner == "openai"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_get_repo_metadata_404_returns_none_not_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    meta = await gh.get_repo_metadata("https://github.com/nonexistent/repo")
    assert meta is None
    await http_client.aclose()


@pytest.mark.asyncio
async def test_unparseable_url_returns_none_without_network_call():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    meta = await gh.get_repo_metadata("not a github url")
    assert meta is None
    assert call_count["n"] == 0  # never even attempted the request
    await http_client.aclose()


@pytest.mark.asyncio
async def test_repo_metadata_is_cached_second_call_no_network():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"owner": {"login": "x"}, "name": "y", "stargazers_count": 5, "forks_count": 1})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    await gh.get_repo_metadata("https://github.com/x/y")
    await gh.get_repo_metadata("https://github.com/x/y")
    assert call_count["n"] == 1  # second call served from cache
    await http_client.aclose()


@pytest.mark.asyncio
async def test_negative_repo_result_also_cached():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(404, json={"message": "Not Found"})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    await gh.get_repo_metadata("https://github.com/x/gone")
    await gh.get_repo_metadata("https://github.com/x/gone")
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_rate_limit_403_propagates_as_rate_limit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}, json={})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    with pytest.raises(RateLimitError):
        await gh.get_repo_metadata("https://github.com/x/y")
    await http_client.aclose()


@pytest.mark.asyncio
async def test_stars_never_negative_in_practice_matches_model_constraint():
    """Cross-check: this client should never surface a negative star count
    (the DB CheckConstraint in models.py is a second line of defense)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"owner": {"login": "x"}, "name": "y", "stargazers_count": 0, "forks_count": 0})

    http_client = _client_with_mock(handler)
    gh = GitHubEnrichmentClient(http_client)
    meta = await gh.get_repo_metadata("https://github.com/x/y")
    assert meta.stargazers_count >= 0
    await http_client.aclose()
