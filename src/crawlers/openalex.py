"""
OpenAlex adapter (Section 10) -- replacement source for the dead
Papers With Code API.

Uses the official OpenAlex REST API (api.openalex.org/works): JSON, no key,
no scraping. OpenAlex asks that unauthenticated clients identify themselves
with a `mailto=` query parameter to enter the "polite pool" (faster, more
reliable shared rate limit); that value is configuration, never hardcoded
to a real address (see Settings.openalex_mailto).

Field discipline (Section 48 / anti-fabrication): every value written here
comes verbatim from the API response. OpenAlex exposes no repository
relation, so `github_url` and `github_stars` are always NULL from this
source -- they are never inferred from a similar name, and `github_stars`
remains the exclusive output of GitHubEnrichmentClient.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError
from src.extraction.dates import parse_absolute_date

# OpenAlex concept C154945302 == "Artificial intelligence".
DEFAULT_FILTER = "concepts.id:C154945302"

# API-documented limits for basic (page/per-page) paging.
MAX_PER_PAGE = 200
MAX_BASIC_PAGING_RESULTS = 10_000

# Only the fields this adapter actually reads are requested, which keeps
# responses small and makes it obvious that nothing else is consumed.
SELECT_FIELDS = "id,doi,title,display_name,publication_date,authorships,primary_location,ids"

_OPENALEX_WORK_ID = re.compile(r"/(W\d+)\s*$")


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    vertical = "research"

    API_BASE = "https://api.openalex.org/works"

    def __init__(
        self,
        http_client,
        *,
        filter_expr: str = DEFAULT_FILTER,
        max_results: int = 100,
        page_size: int = 50,
        mailto: str | None = None,
    ):
        super().__init__(http_client)
        self.filter_expr = filter_expr
        self.max_results = max_results
        self.page_size = max(1, min(page_size, MAX_PER_PAGE))
        self.mailto = mailto

    # ---- discovery -------------------------------------------------------

    def _page_url(self, page: int, per_page: int) -> str:
        url = (
            f"{self.API_BASE}?filter={self.filter_expr}"
            f"&sort=publication_date:desc"
            f"&per-page={per_page}&page={page}"
            f"&select={SELECT_FIELDS}"
        )
        if self.mailto:
            # Polite pool. Only appended when configured -- we never invent
            # a contact address.
            url += f"&mailto={quote(self.mailto, safe='@')}"
        return url

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """OpenAlex paginates via `page` + `per-page`; each discovered item
        is one page of /works, which parse() expands into records.

        Basic paging is capped by the API at 10,000 results
        (page * per-page); beyond that OpenAlex requires cursor paging, so
        we stop rather than silently request pages that would 400.
        """
        wanted = min(self.max_results, MAX_BASIC_PAGING_RESULTS)
        emitted = 0
        page = 1
        while emitted < wanted:
            per_page = min(self.page_size, wanted - emitted)
            yield DiscoveredUrl(
                url=self._page_url(page, per_page),
                metadata={"page": page, "per_page": per_page},
            )
            emitted += per_page
            page += 1

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    # ---- parsing ---------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(
                f"Malformed JSON from OpenAlex: {exc}", context={"url": fetch_result.url}
            ) from exc

        if not isinstance(payload, dict):
            raise ParsingError("Unexpected OpenAlex payload (not a JSON object)", context={"url": fetch_result.url})

        # An OpenAlex error body is `{"error": ..., "message": ...}` with no
        # `results`. Treating that as an empty page would silently hide a
        # broken source, so it is an explicit parse failure instead.
        if "results" not in payload:
            raise ParsingError(
                f"OpenAlex response has no 'results' key (error body?): {payload.get('error') or 'unknown'}",
                context={"url": fetch_result.url},
            )

        results = payload.get("results")
        if not isinstance(results, list):
            raise ParsingError("OpenAlex 'results' is not a list", context={"url": fetch_result.url})

        records: list[ParsedRecord] = []
        for work in results:
            if not isinstance(work, dict):
                continue
            record = self._parse_work(work, fetch_result)
            if record is not None:
                records.append(record)
        return records

    def _parse_work(self, work: dict, fetch_result: FetchResult) -> ParsedRecord | None:
        # OpenAlex returns both `title` and `display_name`; they are the same
        # underlying value, `title` being the canonical alias.
        raw_title = work.get("title") or work.get("display_name") or ""
        title = " ".join(str(raw_title).split())

        paper_url = self._select_paper_url(work)
        if not title or not paper_url:
            # A required field is genuinely absent -- skip the record rather
            # than synthesize a title or URL (Section 48).
            return None

        authors = self._extract_authors(work)

        published_raw = work.get("publication_date")
        published_date = parse_absolute_date(published_raw) if published_raw else None

        openalex_id = work.get("id")
        paper_external_id = None
        if isinstance(openalex_id, str):
            match = _OPENALEX_WORK_ID.search(openalex_id)
            paper_external_id = match.group(1) if match else None

        return ParsedRecord(
            record_type="research_paper",
            data={
                "title": title,
                "authors": authors,
                "paper_url": paper_url,
                "paper_external_id": paper_external_id,
                # OpenAlex has no repository relation. Left NULL rather than
                # guessed; enrichment only ever runs on adapter-supplied
                # explicit links.
                "github_url": None,
                "github_stars": None,
                "published_date": published_date,
            },
            source_name=self.name,
            source_url=paper_url,
            fetch_result=fetch_result,
        )

    @staticmethod
    def _select_paper_url(work: dict) -> str | None:
        """Pick the most stable URL OpenAlex actually returned.

        Preference order: DOI (globally unique and stable) -> the publisher
        landing page -> the OpenAlex work URL. All three are values present
        in the response; nothing is constructed from parts.
        """
        candidates: list[object] = [work.get("doi")]

        primary_location = work.get("primary_location")
        if isinstance(primary_location, dict):
            candidates.append(primary_location.get("landing_page_url"))

        candidates.append(work.get("id"))

        for candidate in candidates:
            if isinstance(candidate, str):
                cleaned = candidate.strip()
                if cleaned.startswith("http://") or cleaned.startswith("https://"):
                    return cleaned
        return None

    @staticmethod
    def _extract_authors(work: dict) -> list[str]:
        authorships = work.get("authorships")
        if not isinstance(authorships, list):
            return []
        authors: list[str] = []
        for authorship in authorships:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author")
            if not isinstance(author, dict):
                continue
            display_name = author.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                authors.append(" ".join(display_name.split()))
        return authors
