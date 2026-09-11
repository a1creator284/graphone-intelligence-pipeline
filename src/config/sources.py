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
        name="openalex",
        vertical=Vertical.RESEARCH,
        base_url="https://api.openalex.org/works",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON REST API (/works, filtered by the 'Artificial intelligence' concept) -> title/authorships/DOI/landing page",
        date_strategy="API 'publication_date' field (date, assumed UTC midnight); null when the work has none",
        rate_limit_notes=(
            "No API key. 100k calls/day, 10 req/s shared pool; sending a `mailto=` "
            "parameter (settings.openalex_mailto) joins the faster polite pool. Back off on 429."
        ),
        known_limitations=(
            "No repository relation at all, so github_url is always NULL from this source. "
            "Basic page/per-page paging is capped at 10,000 results; deeper collection needs cursor paging. "
            "publication_date is a date only (no time) and is occasionally a publisher-supplied future date."
        ),
    ),
    SourceDefinition(
        name="papers_with_code",
        vertical=Vertical.RESEARCH,
        base_url="https://paperswithcode.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON REST API -> paper + linked repository objects",
        date_strategy="API 'published' field (date, assumed UTC midnight)",
        rate_limit_notes="Public API, generous but unauthenticated rate limit; back off on 429.",
        known_limitations=(
            "DEAD UPSTREAM (re-verified 2026-09-09): /api/v1/papers/ returns 302 -> "
            "huggingface.co/papers/trending and serves HTML, not JSON. Disabled rather than "
            "deleted so the historical configuration and the dead-source handling remain visible. "
            "Superseded by the 'openalex' source above. Original limitation: not all papers have "
            "a linked repository; do not infer one."
        ),
        enabled=False,
    ),
    SourceDefinition(
        name="hackernews_ai",
        vertical=Vertical.NEWS,
        base_url="https://hn.algolia.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Algolia search API filtered by AI-related tags/query, then article HTML fetched for full text",
        date_strategy="Destination article datePublished/article:published_time; HN created_at retained only as submission metadata",
        rate_limit_notes="Public, no key. Client-side throttling applied.",
        known_limitations="Bounded to the latest 100 AI-query submissions within 24h. Undated/old destination articles are rejected even when newly submitted.",
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
        known_limitations="Disabled after live check 2026-09-10: newest feed entry is 2025-08-14; cannot supply fresh news. Replaced by thedecoder_rss; retained for audit history.",
        enabled=False,
    ),
    SourceDefinition(
        name="thedecoder_rss",
        vertical=Vertical.NEWS,
        base_url="https://the-decoder.com/feed/",
        discovery=DiscoveryMechanism.RSS_ATOM,
        parsing_strategy="Public RSS -> title/link; full text via existing article page extraction",
        date_strategy="RSS pubDate (timezone-explicit RFC timestamp), never modification time",
        rate_limit_notes="No authentication; one feed poll per run, shared HTTP retry/backoff.",
        known_limitations="Feed is capped to recent items; access failures or empty freshness windows yield no records.",
    ),
    SourceDefinition(
        name="remoteok_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://remoteok.com/api",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="JSON API filtered by ai/ml tags",
        date_strategy="Explicit timezone-aware API date only; never epoch metadata or last_updated",
        rate_limit_notes="Public JSON endpoint; requires a descriptive User-Agent or requests are blocked.",
        known_limitations="Skews toward fully-remote roles; limited enterprise ATS coverage.",
    ),
    SourceDefinition(
        name="workingnomads_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://www.workingnomads.com/api/exposed_jobs",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Public category=data JSON API plus explicit AI/ML/data-science keyword filtering",
        date_strategy="Explicit timezone-aware pub_date only; naive/date-only values rejected",
        rate_limit_notes="Public JSON endpoint, low volume.",
        known_limitations="Category taxonomy is coarser than AI. API timed out during 2026-09-10 live audit; failures yield zero records, never replacement data.",
    ),
    SourceDefinition(
        name="ycombinator_hn_whoishiring",
        vertical=Vertical.JOBS,
        base_url="https://hn.algolia.com/api/v1",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="Algolia search over 'Ask HN: Who is hiring?' threads, comment-level parsing",
        date_strategy="Top-level hiring comment created_at is posting time; monthly thread date is provenance only",
        rate_limit_notes="Public, no key.",
        known_limitations="Sparse most days. Only explicit company/role pipe segments with AI evidence are accepted. HN comment URL is provenance; first external link is retained only as application_url metadata.",
    ),
    SourceDefinition(
        name="wellfound_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://wellfound.com",
        discovery=DiscoveryMechanism.SITEMAP,
        parsing_strategy="Sitemap discovery -> job posting page -> JSON-LD JobPosting schema",
        date_strategy="JSON-LD datePosted field",
        rate_limit_notes="No public API; sitemap + JSON-LD only, no scraping past what's in structured data.",
        known_limitations="2026-09-10: public sitemap returns 403 Access Restricted; no verified public API. Do not bypass blocks. Static JSON-LD only; source may contribute zero. No browser-rendering capability is claimed.",
    ),
    SourceDefinition(
        name="builtin_ai_jobs",
        vertical=Vertical.JOBS,
        base_url="https://builtin.com/jobs/ai-machine-learning",
        discovery=DiscoveryMechanism.HTTP_CRAWL,
        parsing_strategy="Public server-rendered AI category listing (first page only) -> actual /job/ links -> JSON-LD JobPosting",
        date_strategy="Explicit timezone-aware JSON-LD datePosted only, never relative listing labels or validThrough",
        rate_limit_notes="No public API; bounded first-page polling with shared HTTP retry/backoff.",
        known_limitations="2026-09-10: old sitemap URL serves HTML, not XML. Public listing is accessible but sampled datePosted is date-only and therefore rejected. No inferred midnight/timezone or fake replacement.",
    ),
    SourceDefinition(
        name="ycombinator_directory",
        vertical=Vertical.STARTUPS,
        base_url="https://www.ycombinator.com/companies",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy=(
            "Public search-only Algolia index (YCCompany_production) that the YC directory's "
            "own UI queries; AI tag facet filter, batch-partitioned paging"
        ),
        date_strategy="not applicable (entity data, not time-series)",
        rate_limit_notes=(
            "Public search-only key scoped to the ycdc_public tag filter; bounded concurrency "
            "via AsyncHttpClient, one facet query plus paged queries per run."
        ),
        known_limitations=(
            "Algolia caps a single query at 1,000 retrievable hits, so discovery partitions by "
            "YC batch to reach the full AI-tagged population (~1,900). team_size is stored "
            "verbatim as employee_count or left null; it is never estimated. The public key is "
            "browser-visible and YC may rotate it (override via YC_ALGOLIA_API_KEY)."
        ),
    ),
    # --- PRODUCTS -----------------------------------------------------------
    # Order here mirrors src/pipeline/products.ADAPTER_CLASSES, and each `name`
    # matches its adapter's `name` attribute exactly, so the registry and the
    # runtime cannot drift apart.
    SourceDefinition(
        name="huggingface_spaces",
        vertical=Vertical.PRODUCTS,
        base_url="https://huggingface.co/api/spaces",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy=(
            "Official public Hub REST listing (JSON array, full=true) -> deployed, "
            "publicly-listed AI applications. product_name from cardData.title, else the "
            "repo segment of the Hub id; startup_name from the `author` owner field."
        ),
        date_strategy="not applicable (entity data, not time-series); createdAt/lastModified kept verbatim in metadata_json",
        rate_limit_notes=(
            "No auth, no API key. Cursor-paged via the `Link: rel=\"next\"` response header "
            "(the deprecated `skip=` offset is deliberately not used); bounded concurrency "
            "via AsyncHttpClient."
        ),
        known_limitations=(
            "The Hub publishes no price for Spaces, so pricing_model is always NULL from this "
            "source -- it is never keyword-derived from a description. Spaces with neither an "
            "author nor an owner segment are skipped rather than attributed to an invented vendor."
        ),
    ),
    SourceDefinition(
        name="openrouter_models",
        vertical=Vertical.PRODUCTS,
        base_url="https://openrouter.ai/api/v1/models",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy=(
            "Official public model catalogue (JSON {data: [...]}) -> commercial AI model "
            "products. product_name from `name` verbatim; startup_name from the \"Vendor: Model\" "
            "prefix, else the owner segment of the model id."
        ),
        date_strategy="not applicable (catalogue data); the numeric `created` field is kept verbatim in metadata_json",
        rate_limit_notes="Public, unauthenticated listing endpoint; constant page size per run, bounded concurrency.",
        known_limitations=(
            "The only products source publishing real prices, so the only one that may populate "
            "pricing_model (FREE when every published price is 0, PAID otherwise, NULL when the "
            "source published no usable pricing). When a model declares a hugging_face_id, that "
            "repo id becomes the dedup_key so the same artifact listed by Hugging Face collapses "
            "to one row instead of being counted twice."
        ),
    ),
    SourceDefinition(
        name="huggingface_models",
        vertical=Vertical.PRODUCTS,
        base_url="https://huggingface.co/api/models",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy=(
            "Same official Hub REST surface as huggingface_spaces, sorted by the Hub's own "
            "`downloads` metric -> published AI model products. product_name is the repo segment "
            "of the Hub id (model repos rarely carry cardData.title)."
        ),
        date_strategy="not applicable (entity data); createdAt/lastModified kept verbatim in metadata_json",
        rate_limit_notes="No auth; same `Link` cursor paging and bounded concurrency as huggingface_spaces.",
        known_limitations=(
            "No published price, so pricing_model is always NULL. Runs last of the three products "
            "sources, so a model already contributed by openrouter_models under the same "
            "`hf-model:<repo id>` dedup_key is not counted twice."
        ),
    ),
    SourceDefinition(
        name="producthunt_ai_products",
        vertical=Vertical.PRODUCTS,
        base_url="https://api.producthunt.com/v2/api/graphql",
        discovery=DiscoveryMechanism.OFFICIAL_API,
        parsing_strategy="GraphQL API, topic=artificial-intelligence",
        date_strategy="featuredAt field, ISO-8601",
        rate_limit_notes="Requires an OAuth developer token; token-bucket rate limit enforced by API.",
        known_limitations=(
            "DISABLED: there is no adapter for this source in src/crawlers/ and the API requires "
            "an OAuth bearer token that this deployment does not hold, so enabling it would "
            "advertise a capability the pipeline cannot execute. Kept as documentation of the "
            "evaluated option; the three unauthenticated sources above cover the vertical. "
            "Original limitation: pricingModel is not a native field and would have to be derived "
            "from tagline/description keywords, which Section 24 forbids."
        ),
        enabled=False,
    ),
]


def get_sources_for_vertical(vertical: Vertical) -> list[SourceDefinition]:
    return [s for s in SOURCE_REGISTRY if s.vertical == vertical and s.enabled]
