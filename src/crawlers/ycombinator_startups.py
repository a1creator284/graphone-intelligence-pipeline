"""
Y Combinator company-directory adapter (startups vertical, Section 10).

Discovery mechanism
-------------------
`ycombinator.com/companies` is a client-rendered page whose own search UI
talks to a **public, read-only Algolia index** (`YCCompany_production`). The
application id and search-only API key are shipped to every browser in the
page source as `window.AlgoliaOpts`; this adapter uses exactly the same
public endpoint the site's own directory uses, over the documented Algolia
REST search API. No login, no scraping of rendered HTML, no bypassing of any
access control.

The key is a *search-only* key scoped by Algolia to the `ycdc_public` tag
filter, so it can only read what the public directory already shows. It is
configuration (`Settings.yc_algolia_api_key`), not a secret, and it can be
rotated by YC at any time -- the adapter reads it from settings so a refresh
needs no code change.

Pagination / scale
------------------
Algolia caps any single query at 1,000 retrievable hits
(`paginationLimitedTo`), and the AI-tagged slice of the directory is larger
than that. Rather than silently truncating, discovery **partitions the index
by YC batch** (a facet on the same index): one facet query returns every
batch name and its hit count, then each batch is paged independently. No
individual batch is anywhere near the 1,000-hit cap, so the partitioned
crawl can reach the full tagged population (currently ~1,900 companies)
without any code change. If the facet query fails, discovery degrades to a
single unpartitioned paged crawl bounded by the 1,000-hit cap rather than
issuing requests that would error.

Field discipline (Section 48 / anti-fabrication)
------------------------------------------------
Every value written comes verbatim from the API response:

* ``entity_name``     <- ``name``
* ``employee_count``  <- ``team_size``, only when it is a non-negative int;
  ``null`` otherwise. YC's own directory leaves this blank for some
  companies and it is never estimated, rounded, or back-filled here.
* ``source_url``      <- the canonical YC directory URL for the company,
  formed from the ``slug`` the API returns. A company with no ``slug`` is
  skipped rather than given a guessed URL.

No founder names, funding amounts, valuations, or descriptions are
synthesized. Fields the API does not return are simply absent.
"""
from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from src.config.logging import get_logger
from src.crawlers.base import DiscoveredUrl, ParsedRecord, SourceAdapter
from src.crawlers.http import FetchResult
from src.errors import ParsingError, PipelineError

logger = get_logger(component="yc_startups_adapter")

# Public, browser-visible Algolia credentials from window.AlgoliaOpts on
# https://www.ycombinator.com/companies. Search-only, tag-scoped to
# ycdc_public. Overridable via settings so a YC rotation needs no code edit.
DEFAULT_ALGOLIA_APP_ID = "45BWZJ1SGC"
DEFAULT_ALGOLIA_API_KEY = (
    "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRjNzJiMWM4ZTU0"
    "ZDlhYTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZyZXN0cmljdEluZGljZXM9WUND"
    "b21wYW55X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVj"
    "dGlvbiZ0YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
)

INDEX_NAME = "YCCompany_production"

# Tags are OR-ed inside one facetFilters group. These are the AI-relevant
# tag values that actually exist in the index's `tags` facet (verified
# against the live facet listing) -- not guesses.
DEFAULT_AI_TAGS = (
    "Artificial Intelligence",
    "AI",
    "Generative AI",
    "Machine Learning",
)

# Algolia's per-query retrievable-hit ceiling for this index.
MAX_HITS_PER_QUERY = 1000
MAX_HITS_PER_PAGE = 1000

YC_COMPANY_URL_TEMPLATE = "https://www.ycombinator.com/companies/{slug}"


class YCombinatorStartupsAdapter(SourceAdapter):
    name = "ycombinator_directory"
    vertical = "startups"

    def __init__(
        self,
        http_client,
        *,
        app_id: str = DEFAULT_ALGOLIA_APP_ID,
        api_key: str = DEFAULT_ALGOLIA_API_KEY,
        tags: tuple[str, ...] = DEFAULT_AI_TAGS,
        max_results: int = 100,
        page_size: int = 100,
        partition_by_batch: bool = True,
    ):
        super().__init__(http_client)
        self.app_id = app_id
        self.api_key = api_key
        self.tags = tuple(tags)
        self.max_results = max_results
        self.page_size = max(1, min(page_size, MAX_HITS_PER_PAGE))
        self.partition_by_batch = partition_by_batch

    # ---- URL construction -------------------------------------------------

    @property
    def api_base(self) -> str:
        return f"https://{self.app_id.lower()}-dsn.algolia.net/1/indexes/{INDEX_NAME}"

    def _tag_filter_group(self) -> list[str]:
        return [f"tags:{tag}" for tag in self.tags]

    def _query_url(
        self,
        *,
        page: int,
        hits_per_page: int,
        batch: str | None = None,
        facets: list[str] | None = None,
    ) -> str:
        facet_filters: list[list[str]] = [self._tag_filter_group()]
        if batch is not None:
            facet_filters.append([f"batch:{batch}"])

        params: dict[str, str] = {
            "x-algolia-application-id": self.app_id,
            "x-algolia-api-key": self.api_key,
            "query": "",
            "hitsPerPage": str(hits_per_page),
            "page": str(page),
            "facetFilters": json.dumps(facet_filters),
        }
        if facets:
            params["facets"] = json.dumps(facets)
            params["maxValuesPerFacet"] = "1000"
        return f"{self.api_base}?{urlencode(params)}"

    # ---- discovery --------------------------------------------------------

    async def _fetch_batch_counts(self) -> dict[str, int]:
        """One facet query -> {batch name: hit count} for the tagged slice.

        Returns an empty dict (never a guess) if the response is unusable;
        the caller then falls back to unpartitioned paging.
        """
        url = self._query_url(page=0, hits_per_page=0, facets=["batch"])
        try:
            result = await self.http_client.get(url)
            payload = json.loads(result.text)
        except (PipelineError, json.JSONDecodeError) as exc:
            logger.warning("yc_batch_facet_query_failed", error=str(exc))
            return {}

        facets = payload.get("facets") if isinstance(payload, dict) else None
        batches = facets.get("batch") if isinstance(facets, dict) else None
        if not isinstance(batches, dict):
            logger.warning("yc_batch_facet_missing", note="falling back to unpartitioned paging")
            return {}
        return {
            str(name): int(count)
            for name, count in batches.items()
            if isinstance(count, int) and count > 0
        }

    async def discover(self) -> AsyncIterator[DiscoveredUrl]:
        """Yield one DiscoveredUrl per index page; parse() expands each into
        company records.

        Batch partitioning is what lets this exceed Algolia's 1,000-hit
        per-query cap, so the same code scales to the full tagged directory.
        """
        wanted = max(0, self.max_results)
        if wanted == 0:
            return

        batch_counts: dict[str, int] = {}
        if self.partition_by_batch:
            batch_counts = await self._fetch_batch_counts()

        if not batch_counts:
            # Unpartitioned fallback: bounded by Algolia's hard per-query cap
            # so we never issue a page request the API would reject.
            capped = min(wanted, MAX_HITS_PER_QUERY)
            pages = math.ceil(capped / self.page_size)
            for page in range(pages):
                # hitsPerPage is held constant so Algolia's offset
                # (page * hitsPerPage) keeps advancing -- see the partitioned
                # branch below for why shrinking it re-reads earlier hits.
                yield DiscoveredUrl(
                    url=self._query_url(page=page, hits_per_page=self.page_size),
                    metadata={
                        "page": page,
                        "hits_per_page": self.page_size,
                        "batch": None,
                        "max_records": min(self.page_size, capped - page * self.page_size),
                    },
                )
            return

        # Deterministic partition order: largest batches first, name as the
        # tie-break, so two runs with the same target crawl the same pages.
        ordered = sorted(batch_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        emitted = 0
        for batch, count in ordered:
            if emitted >= wanted:
                break
            available = min(count, MAX_HITS_PER_QUERY)
            take = min(available, wanted - emitted)
            # With a uniform page size, the last page's window is
            # `page * page_size + page_size`; keep that within Algolia's
            # retrievable-hit ceiling rather than issuing a query it rejects.
            pages = min(
                math.ceil(take / self.page_size),
                MAX_HITS_PER_QUERY // self.page_size,
            )
            for page in range(pages):
                # `hitsPerPage` MUST stay constant across the pages of a
                # partition: Algolia derives the offset as
                # `page * hitsPerPage`, so shrinking it on the final page
                # slides the window *backwards* and re-serves companies
                # already seen on the previous page (e.g. 100 then 62 over a
                # 162-hit batch re-reads hits 62-99). Those re-reads then
                # collapse on the (source_name, source_url) unique key and
                # were being counted as "duplicates", starving the target.
                # The target ceiling is honoured by `max_records` instead,
                # which trims the final page during parse.
                yield DiscoveredUrl(
                    url=self._query_url(
                        page=page, hits_per_page=self.page_size, batch=batch
                    ),
                    metadata={
                        "page": page,
                        "hits_per_page": self.page_size,
                        "batch": batch,
                        "max_records": min(self.page_size, take - page * self.page_size),
                    },
                )
            emitted += take

    async def fetch(self, discovered: DiscoveredUrl) -> FetchResult:
        return await self.http_client.get(discovered.url)

    # ---- parsing ----------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, discovered: DiscoveredUrl) -> list[ParsedRecord]:
        try:
            payload = json.loads(fetch_result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError(
                f"Malformed JSON from YC Algolia: {exc}", context={"url": fetch_result.url}
            ) from exc

        if not isinstance(payload, dict):
            raise ParsingError(
                "Unexpected YC Algolia payload (not a JSON object)", context={"url": fetch_result.url}
            )

        # An Algolia error body is {"message": ..., "status": ...} with no
        # `hits`. Returning [] for that would make a broken source look like
        # a quiet one, so it is an explicit parse failure.
        if "hits" not in payload:
            raise ParsingError(
                f"YC Algolia response has no 'hits' key (error body?): "
                f"{payload.get('message') or 'unknown'}",
                context={"url": fetch_result.url},
            )

        hits = payload.get("hits")
        if not isinstance(hits, list):
            raise ParsingError("YC Algolia 'hits' is not a list", context={"url": fetch_result.url})

        # Discovery keeps `hitsPerPage` uniform so Algolia's offset stays
        # correct, and asks here for the slice of the final page that is
        # actually still wanted. Trimming real hits we did not ask for keeps
        # the target a ceiling; it never pads.
        max_records = discovered.metadata.get("max_records")
        limit = max_records if isinstance(max_records, int) and max_records >= 0 else None

        records: list[ParsedRecord] = []
        seen_urls: set[str] = set()
        for hit in hits:
            if limit is not None and len(records) >= limit:
                break
            if not isinstance(hit, dict):
                continue
            record = self._parse_company(hit, fetch_result)
            if record is None:
                continue
            # Deterministic in-page dedup; cross-page dedup is by source_url
            # in the pipeline and by the DB unique constraint.
            if record.source_url in seen_urls:
                continue
            seen_urls.add(record.source_url)
            records.append(record)
        return records

    def _parse_company(self, hit: dict, fetch_result: FetchResult) -> ParsedRecord | None:
        raw_name = hit.get("name")
        name = " ".join(str(raw_name).split()) if isinstance(raw_name, str) else ""

        source_url = self._company_url(hit)
        if not name or not source_url:
            # A required field is genuinely absent -- skip rather than
            # synthesize a company name or directory URL.
            return None

        return ParsedRecord(
            record_type="startup",
            data={
                "entity_name": name,
                "employee_count": self._employee_count(hit),
            },
            source_name=self.name,
            source_url=source_url,
            fetch_result=fetch_result,
        )

    @staticmethod
    def _company_url(hit: dict) -> str | None:
        """Canonical YC directory URL for the company.

        Built from the ``slug`` the API returns -- the slug is the record's
        own first-party identifier and this is the exact URL the YC directory
        links to. A record without a usable slug is dropped; no URL is
        guessed from the company name.
        """
        slug = hit.get("slug")
        if not isinstance(slug, str):
            return None
        slug = slug.strip().strip("/")
        if not slug or "/" in slug:
            return None
        return YC_COMPANY_URL_TEMPLATE.format(slug=slug)

    @staticmethod
    def _employee_count(hit: dict) -> int | None:
        """`team_size` verbatim, or NULL.

        YC leaves this blank for some companies and occasionally returns a
        non-integer. Anything that is not a plain non-negative int becomes
        NULL -- it is never defaulted to 0, estimated from the batch, or
        otherwise invented.
        """
        team_size = hit.get("team_size")
        if isinstance(team_size, bool):
            return None
        if isinstance(team_size, int) and team_size >= 0:
            return team_size
        if isinstance(team_size, str):
            stripped = team_size.strip()
            if stripped.isdigit():
                return int(stripped)
        return None
