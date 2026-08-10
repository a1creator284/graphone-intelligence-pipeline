"""
GitHub repository enrichment (Section 11).

Uses the official GitHub REST API. Never infers a repository from a similar
name -- callers must supply an explicit, already-discovered repo URL (e.g.
one an author put in an arXiv abstract). Caches responses in-process per run
so the same repo is never requested twice (Section 11: "Do not request the
same repository repeatedly").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.config.logging import get_logger
from src.config.settings import get_settings
from src.crawlers.http import AsyncHttpClient
from src.errors import BlockedSourceError, NetworkError, PipelineError, RateLimitError, TimeoutErrorPipeline

logger = get_logger(component="github_enrichment")

_GITHUB_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass(slots=True)
class RepoMetadata:
    repo_url: str
    owner: str
    name: str
    stargazers_count: int
    forks_count: int
    updated_at: str | None
    found: bool = True


def parse_github_repo_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Returns None if the URL
    isn't a recognizable GitHub repo URL -- callers must not guess further."""
    if not url:
        return None
    match = _GITHUB_URL_PATTERN.match(url.strip())
    if not match:
        return None
    return match.group("owner"), match.group("repo")


class GitHubEnrichmentClient:
    def __init__(self, http_client: AsyncHttpClient, *, github_token: str | None = None, api_base_url: str | None = None):
        settings = get_settings()
        self.http_client = http_client
        self.github_token = github_token if github_token is not None else settings.github_token
        self.api_base_url = (api_base_url or settings.github_api_base_url).rstrip("/")
        self._cache: dict[str, RepoMetadata | None] = {}

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def get_repo_metadata(self, repo_url: str) -> RepoMetadata | None:
        """Return metadata for a GitHub repo, or None if it doesn't exist /
        can't be verified. Never raises for a plain 404 -- that's a valid
        "no repo" outcome, not a pipeline error (Section 11 handles 403/404/
        429/network errors explicitly).
        """
        parsed = parse_github_repo_url(repo_url)
        if parsed is None:
            logger.warning("github_url_not_parseable", repo_url=repo_url)
            return None
        owner, repo = parsed

        cache_key = f"{owner}/{repo}".lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        api_url = f"{self.api_base_url}/repos/{owner}/{repo}"
        try:
            result = await self.http_client.get(api_url, headers=self._headers())
        except BlockedSourceError as exc:
            logger.warning("github_repo_blocked", repo_url=repo_url, error=str(exc))
            self._cache[cache_key] = None
            return None
        except RateLimitError:
            # Bubble up -- the caller/orchestrator should decide whether to
            # pause enrichment entirely rather than silently skipping every
            # remaining repo (which would look like "no repos found").
            raise
        except (NetworkError, TimeoutErrorPipeline) as exc:
            logger.warning("github_repo_fetch_failed", repo_url=repo_url, error=str(exc))
            self._cache[cache_key] = None
            return None

        if result.status_code == 404:
            self._cache[cache_key] = None
            return None

        import json

        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Non-JSON response from GitHub for {repo_url}") from exc

        metadata = RepoMetadata(
            repo_url=repo_url,
            owner=data.get("owner", {}).get("login", owner),
            name=data.get("name", repo),
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
            updated_at=data.get("updated_at"),
        )
        self._cache[cache_key] = metadata
        logger.info("github_repo_enriched", repo_url=repo_url, stars=metadata.stargazers_count)
        return metadata
