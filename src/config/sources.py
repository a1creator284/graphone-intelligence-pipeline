"""
Declarative source registry.

Each entry documents the discovery mechanism, parsing strategy, and known
limitations for a source, per assessment Section 47. This is metadata only;
the actual adapter implementations live in src/crawlers/.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, HttpUrl


class Vertical(StrEnum):
    RESEARCH = "research"
    STARTUPS = "startups"
    PRODUCTS = "products"
    NEWS = "news"
    JOBS = "jobs"


class DiscoveryMechanism(StrEnum):
    OFFICIAL_API = "official_api"
    RSS_ATOM = "rss_atom"
    SITEMAP = "sitemap"
    HTTP_CRAWL = "http_crawl"
    BROWSER_RENDER = "browser_render"


class SourceDefinition(BaseModel):
    name: str
    vertical: Vertical
    base_url: HttpUrl
    discovery: DiscoveryMechanism
    parsing_strategy: str
    date_strategy: str
    rate_limit_notes: str
    known_limitations: str
    enabled: bool = True


# NOTE: this list documents which sources this codebase is *wired* to
# support. In this sandbox only api.github.com is reachable; the others
# require deployment to an environment with normal internet egress
# (see README "Running outside the sandbox").
SOURCE_REGISTRY: list[SourceDefinition] = [
    SourceDefinition(
        name="arxiv",
        vertical=Vertical.RESEARCH,
        base_url="https://export.arxiv.org/api/query",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Atom XML feed -> title/authors/abstract/links",
        date_strategy="Atom <published>/<updated> fields (ISO-8601 already)",
        rate_limit_notes="No auth required; arXiv asks for <=1 req/3s and a descriptive User-Agent.",
        known_limitations="GitHub links are only present when an author includes one in the abstract or comment field; not guaranteed.",
    ),
    SourceDefinition(
        name="papers_with_code",
        vertical=Vertical.RESEARCH,
        base_url="https://paperswithcode.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON REST API -> paper + linked repository objects",
        date_strategy="API 'published' field (date, assumed UTC midnight)",
        rate_limit_notes="Public API, generous but unauthenticated rate limit; back off on 429.",
        known_limitations="Not all papers have a linked repository; do not infer one.",
    ),
    SourceDefinition(
        name="hackernews_ai",
        vertical=Vertical.NEWS,
        base_url="https://hn.algolia.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Algolia search API filtered by AI-related tags/query, then article HTML fetched for full text",
        date_strategy="created_at field on story JSON (ISO-8601, UTC)",
        rate_limit_notes="Public, no key. Client-side throttling applied.",
        known_limitations="Freshness is measured against submission time, not underlying article publish time.",
    ),
    SourceDefinition(
        name="techcrunch_ai_rss",
        vertical=Vertical.NEWS,
        base_url="https://techcrunch.com/category/artificial-intelligence/feed/",
        discovery=DiscoveryMechanism.RSS_ATOM,
        parsing_strategy="RSS 2.0 -> title/link/description; full text via article page extraction (trafilatura)",
        date_strategy="RSS <pubDate> (RFC 822) normalized to UTC",
        rate_limit_notes="Standard polite crawling; single feed poll per run.",
        known_limitations="Feed is capped to recent items; older items are unreachable via this adapter.",
    ),
    SourceDefinition(
        name="theverge_ai_rss",
        vertical=Vertical.NEWS,
        base_url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        discovery=DiscoveryMechanism.RSS_ATOM,
        parsing_strategy="RSS 2.0 -> title/link/description; full text via article page extraction",
        date_strategy="RSS <pubDate> normalized to UTC",
        rate_limit_notes="Standard polite crawling.",
        known_limitations="Feed availability and article access may vary by source-side changes.",
    ),
    SourceDefinition(
        name="mit_technology_review_ai_rss",
        vertical=Vertical.NEWS,
        base_url="https://www.technologyreview.com/topic/artificial-intelligence/feed",
        discovery=DiscoveryMechanism.RSS_ATOM,
        parsing_strategy="RSS 2.0 -> title/link/description",
        date_strategy="RSS <pubDate> normalized to UTC",
        rate_limit_notes="Standard polite crawling.",
        known_limitations="Lower publication frequency than TechCrunch.",
    ),
    SourceDefinition(
        name="synced_review_rss",
        vertical=Vertical.NEWS,
        base_url="https://syncedreview.com/feed/",
        discovery=DiscoveryMechanism.RSS_ATOM,
        parsing_strategy="RSS 2.0 -> title/link/description",
        date_strategy="RSS <pubDate> normalized to UTC",
        rate_limit_notes="Standard polite crawling.",
        known_limitations="Feed occasionally includes sponsored content; not filtered automatically.",
    ),
    SourceDefinition(
        name="remoteok_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://remoteok.com/api",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON API filtered by ai/ml tags",
        date_strategy="date field is ISO-8601 UTC already",
        rate_limit_notes="Public JSON endpoint; requires a descriptive User-Agent or requests are blocked.",
        known_limitations="Skews toward fully-remote roles; limited enterprise ATS coverage.",
    ),
    SourceDefinition(
        name="workingnomads_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://www.workingnomads.com/api/exposed_jobs",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON API filtered by category=data",
        date_strategy="pub_date field, format varies -> routed through date engine",
        rate_limit_notes="Public JSON endpoint, low volume.",
        known_limitations="Category taxonomy is coarser than 'AI'; adapter applies keyword filtering.",
    ),
    SourceDefinition(
        name="ycombinator_hn_whoishiring",
        vertical=Vertical.JOBS,
        base_url="https://hn.algolia.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Algolia search over 'Ask HN: Who is hiring?' threads, comment-level parsing",
        date_strategy="created_at on the top-level story caps freshness for all child comments",
        rate_limit_notes="Public, no key.",
        known_limitations="Only fresh within the ~24h after the monthly thread posts; sparse most days.",
    ),
    SourceDefinition(
        name="wellfound_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://wellfound.com",
        discovery=DiscoveryMechanism.SITEMAP,
        parsing_strategy="Sitemap discovery -> job posting page -> JSON-LD JobPosting schema",
        date_strategy="JSON-LD datePosted field",
        rate_limit_notes="No public API; sitemap + JSON-LD only, no scraping past what's in structured data.",
        known_limitations="Listing pages behind JS rendering require the Playwright tier; static fetch may miss postings.",
    ),
    SourceDefinition(
        name="builtin_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://builtin.com/jobs/ai-machine-learning",
        discovery=DiscoveryMechanism.SITEMAP,
        parsing_strategy="Sitemap discovery -> job posting page -> JSON-LD JobPosting schema",
        date_strategy="JSON-LD datePosted field",
        rate_limit_notes="No public API.",
        known_limitations="Requires JSON-LD presence; postings without structured data are skipped, not guessed.",
    ),
    SourceDefinition(
        name="ycombinator_directory",
        vertical=Vertical.STARTUPS,
        base_url="https://www.ycombinator.com/companies",
        discovery=DiscoveryMechanism.HTTP_CRAWL,
        parsing_strategy="Public company directory JSON endpoint used by the site's own search UI",
        date_strategy="not applicable (entity data, not time-series)",
        rate_limit_notes="Client-side throttled; single paginated crawl per run.",
        known_limitations="Employee count is a banded estimate on YC's own site, not exact; stored as provided or null.",
    ),
    SourceDefinition(
        name="producthunt_ai_products",
        vertical=Vertical.PRODUCTS,
        base_url="https://api.producthunt.com/v2/api/graphql",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="GraphQL API, topic=artificial-intelligence",
        date_strategy="featuredAt field, ISO-8601",
        rate_limit_notes="Requires OAuth token; token-bucket rate limit enforced by API.",
        known_limitations="pricingModel is not a native field; derived deterministically from tagline/description keywords, or left null if ambiguous (never LLM-guessed per Section 24).",
    ),
]


def get_sources_for_vertical(vertical: Vertical) -> list[SourceDefinition]:
    return [s for s in SOURCE_REGISTRY if s.vertical == vertical and s.enabled]
