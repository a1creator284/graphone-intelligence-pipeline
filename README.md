# GraphOne / FrontierAtlas Intelligence Pipeline

Async, fault-tolerant ingestion pipeline for AI research papers, startups,
products, news, and jobs. Built for the GraphOne / FrontierAtlas AI Engineer
assessment.

## Project status (honest, not aspirational)

This repo is being built in phases (see `docs/DEVELOPMENT_HANDOFF.md` for the
full phase-by-phase log). **Phases 1-8 are implemented and test-verified as
of this commit (249 passing tests):**

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo foundation, settings, structured logging, Docker | ✅ Done |
| 2 | Database models (14 tables), repositories, idempotent upserts | ✅ Done |
| 3 | Async crawler core: HTTP client, retry/backoff, worker pool | ✅ Done |
| 4 | Research paper pipeline: arXiv + Papers With Code + GitHub enrichment | ✅ Done |
| 5 | Deterministic date engine + 24h freshness utility | ✅ Done |
| 6 | News pipeline: 5 API/RSS sources, validation, freshness filtering | ✅ Done |
| 7 | Jobs pipeline: 5 API/Sitemap sources, JSON-LD, validation, provenance | ✅ Done |
| 8 | LLM orchestration: 3 providers, fallback, chunking, 413 handling, metrics | ✅ Done |
| 9-16 | Startups, products, entity resolution, quality metrics, export, audit | ⏳ Not yet built |

`python -m src.main --vertical research` and `--vertical news` **actually run**
end-to-end (discovery → extraction/enrichment → schema validation →
idempotent Postgres/SQLite persistence), verified against live networks — see
"Live verification" below. Startups/products verticals still just report their
registered sources; those adapters land in Phases 9-11.

## Live verification (updated — network egress is now available)

Earlier phases of this project were developed in a sandbox whose egress was
restricted to `pypi.org`, `npmjs.org`, and `github.com`. **That restriction
no longer applies in the current environment**, and the pipelines have now
been run against real, live sources. Earlier README/handoff text describing
arXiv and the RSS feeds as unreachable is superseded by this section.

Verified live in this environment:

| Source | Result |
|---|---|
| `export.arxiv.org` Atom API | ✅ `200 OK`, 1 real paper parsed and persisted |
| `hn.algolia.com` | ✅ reachable (0 records inside the 24h freshness window at run time) |
| TechCrunch AI RSS | ✅ 1 real article discovered, extracted, persisted |
| The Verge AI RSS | ✅ 1 real article discovered, extracted, persisted |
| MIT Tech Review AI RSS | ✅ 1 real article discovered, extracted, persisted |
| Synced Review RSS | ✅ 1 real article discovered (rejected: outside 24h window) |
| `remoteok.com/api` | ✅ `200 OK` |
| `paperswithcode.com` | ❌ **dead — see below** |
| `api.github.com` | ✅ live enrichment verified in earlier phases |

Actual live news run (`--vertical news --target 5`):

```
discovered: 4, fetched: 4, full_text_extracted: 3, validated: 3,
rejected_stale: 1, rejected_invalid: 0, persisted: 3
```

Note the honest accounting: 4 discovered but only 3 persisted, because one
article fell outside the 24-hour freshness window and was **rejected rather
than padded** (Section 48, "No Fake Success").

### Known upstream breakage: Papers With Code is gone

`paperswithcode.com/api/v1/papers/` now `302`-redirects to
`huggingface.co/papers/trending` and returns **HTML, not JSON** — Papers With
Code was retired upstream. The adapter behaves correctly under this failure:
it raises `ParsingError` ("Malformed JSON from Papers With Code"), logs
`parse_failed`, contributes `0` records, and lets the rest of the run
continue rather than crashing or inventing data:

```
{"source": "papers_with_code", "discovered": 1, "fetched": 1,
 "fetch_failed": 1, "parsed_records": 0, ...}
{"by_source": {"arxiv": 1, "papers_with_code": 0}}
```

This is correct fail-loud behavior, but the source is permanently dead and
should be **replaced** (HuggingFace Papers or Semantic Scholar are the
natural successors). Tracked as the top item in "Next phases".

## A bug this approach already caught

While verifying Phase 3 against the one live endpoint this sandbox can
reach, `GET https://api.github.com/repos/pytorch/pytorch` returned `403`
because of anonymous rate-limit exhaustion — not a real access block. The
original code classified all `403`s as `BlockedSourceError` (permanent,
never retried). That's wrong: GitHub signals rate-limiting via `403` +
`X-RateLimit-Remaining: 0` rather than a `429`. Fixed in
`src/crawlers/http.py`, with a fast mocked regression test in
`tests/test_http_client.py` locking in both cases (genuine 403 vs.
rate-limited 403). This is exactly the kind of bug that only shows up by
actually calling something real, which is why Phase 3 wasn't marked done
until that call was made.

## Tech stack

Python 3.11+ (verified on 3.13) · asyncio · httpx · Pydantic v2 · SQLAlchemy 2.x (async) ·
PostgreSQL · Redis · pytest/pytest-asyncio · respx · structlog ·
dateparser/python-dateutil · Docker Compose. See `requirements.txt` for
pinned versions.

## Repository structure

```
src/
  config/       settings, source registry, logging
  crawlers/     HTTP client, retry/backoff, base adapter interface,
                arxiv.py, papers_with_code.py
  extraction/   dates.py (date engine + freshness), github.py (enrichment), urls.py
  validation/   schemas.py (Pydantic record validation)
  storage/      SQLAlchemy models, async engine, repositories
  pipeline/     worker pool + research.py (research vertical orchestration)
  errors.py     typed error hierarchy
tests/          unit tests (no live network) + tests/test_http_integration.py (opt-in, live)
  fixtures/     realistic arXiv/Papers With Code API response fixtures
```
(`llm/`, `resolution/`, `export/` exist as package stubs for Phases 8+.)

## Setup

```bash
cp .env.example .env        # fill in DB/Redis URLs and any LLM/GitHub keys
pip install -r requirements.txt
docker compose up -d postgres redis
```

## Running tests

```bash
pytest                      # unit tests only, no live network required (249 tests)
pytest -m integration       # opt-in: hits the real GitHub API (2 tests)
```

## Database setup

Models are defined in `src/storage/models.py`. `python -m src.main
--vertical research` creates tables automatically via `Base.metadata.
create_all` on startup. For manual setup:

```bash
python -c "
import asyncio
from src.storage.database import init_engine
from src.storage.models import Base

async def main():
    engine = init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

asyncio.run(main())
"
```

(A proper Alembic migration chain is planned before production use; direct
`create_all` is fine for the assessment demo.)

## Running the CLI

```bash
python -m src.main --help
python -m src.main --vertical research --target 1000 --workers 50   # runs end-to-end
python -m src.main --vertical news --dry-run                        # reports registered sources only (Phase 6+)
```

## Research paper pipeline (Phase 4)

`src/pipeline/research.py` orchestrates:

1. `ArxivAdapter` (`src/crawlers/arxiv.py`) — official arXiv Atom API,
   paginated discovery, extracts title/authors/paper_url/published_date,
   and pulls a GitHub URL **only** when one is explicitly mentioned in the
   abstract/comment text (never inferred from a similar name).
2. `PapersWithCodeAdapter` (`src/crawlers/papers_with_code.py`) — official
   PWC JSON REST API, uses PWC's own `repository` relation when present.
3. `GitHubEnrichmentClient` (`src/extraction/github.py`) — resolves each
   discovered repo URL against the real GitHub API, in-process cached so a
   repo is never requested twice per run; a 404 nulls out the link rather
   than keeping an unconfirmed one; a rate limit (429, or GitHub's 403 +
   `X-RateLimit-Remaining: 0`) disables further enrichment for the rest of
   the run and leaves `github_stars: null` rather than guessing.
4. `validate_research_paper` (`src/validation/schemas.py`) — Pydantic gate;
   rejects records with a missing/blank title, an unparseable `paper_url`,
   a malformed `github_url`, or a negative `github_stars` (the DB
   `CheckConstraint` is a second line of defense for the last one).
5. `RawDocumentRepository` — provenance persistence, deduplicated on
   content hash (Section 8).
6. `ResearchPaperRepository` — idempotent `INSERT ... ON CONFLICT DO
   NOTHING` on `paper_url`, globally unique across both source adapters.

## News pipeline (Phase 6)

`python -m src.main --vertical news --target 1000 --workers 50` orchestrates high-fidelity AI news ingestion across five sources:

1. `HackerNewsAIAdapter` — Algolia API filtered for AI-related queries.
2. `TechCrunchAIAdapter` — TechCrunch AI RSS feed.
3. `TheVergeAIAdapter` — The Verge AI RSS feed.
4. `MITTechReviewAIAdapter` — MIT Technology Review AI RSS feed.
5. `SyncedReviewAdapter` — Synced Review RSS feed.

**Key Features:**
- **Anti-Hallucination Guarantees:** Missing data results in rejection, never fabrication. If full-text extraction fails or critical metadata is absent, the record is discarded (`extraction_failed` or `invalid_records`).
- **Freshness Filtering:** Implements strict 24-hour window validation (`is_fresh`) using `Date_Engine`, with a configurable clock skew tolerance to handle minor server clock drift (e.g., rejecting future-dated articles).
- **Full-Text Extraction Requirement:** Uses `trafilatura` (with `newspaper3k` fallback) to extract article text. Enforces a minimum of 100 characters, automatically rejecting stub articles.
- **Deduplication Strategy:** Implements URL-based per-source deduplication for news records (unlike global URL deduplication for research), and content-hash deduplication for raw document provenance.
- **Content-Addressable Storage:** Deterministic SHA-256 hash-based local storage for full-text, ensuring the system is migration-ready for S3/MinIO.

**Environment Note:** The news endpoints are unreachable from the initial restricted sandbox environment (egress restricted to pypi, npmjs, github). Adapters will be verified against live sources post-deployment.

## Jobs pipeline (Phase 7)

`python -m src.main --vertical jobs --target 1000 --workers 50` orchestrates job ingestion across five sources:

1. `RemoteOKAIAdapter` — JSON API filtered by AI/ML tags.
2. `WorkingNomadsAIAdapter` — JSON API filtered for data/AI categories.
3. `YCombinatorWhoIsHiringAdapter` — Algolia search targeting "Ask HN: Who is hiring?" thread comments.
4. `WellfoundAIAdapter` — XML Sitemap discovery targeting `JobPosting` JSON-LD schema on job pages.
5. `BuiltInAIAdapter` — XML Sitemap discovery targeting `JobPosting` JSON-LD schema.

**Key Features:**
- **JSON-LD Structured Extraction:** Relies strictly on `application/ld+json` schema standard blocks for sitemap-based adapters. Eliminates hallucination (e.g., guessing missing dates) by explicitly rejecting properties not structurally encoded in the DOM.
- **Provenance Linkage:** The payload of any job fetching operation is immutably stored in the `RawDocumentRepository` immediately prior to structured persistence, binding the raw payload explicitly to the `JobRecord` using the `raw_document_id` foreign key.
- **Anti-Hallucination:** Heuristics strictly fallback to nothing if parsing boundaries fail. Required properties like URL, title, source name, and posted date must resolve accurately.
- **Freshness Validation:** Utilizes the shared `Date_Engine` (Phase 5) and strict 24-hour window configuration via `is_fresh` filtering. Future-dated bounds and excessively stale limits correctly trap out-of-bounds postings natively.
- **Database-Level Deduplication:** Uses `INSERT ... ON CONFLICT DO NOTHING` on the exact `(source_name, url)` pair matching mechanism ensuring DB idempotency natively supports repeated idempotent CLI crawling.
- **Deterministic Testing:** Mocked adapters run without relying on live website networks, ensuring `pytest` pipelines run flawlessly within sandbox egress bounds.

## LLM orchestration engine (Phase 8)

`src/llm/orchestrator.py` is the central engine that every LLM-dependent
phase (startups/products enrichment, entity resolution) will route through.
It is provider-agnostic and returns **validated Pydantic models**, never raw
text.

### Providers and fallback

Three providers implement the common `LLMProvider` interface
(`src/llm/providers/base.py`), each wrapping the project's own
`AsyncHttpClient` rather than a vendor SDK, so retry/backoff and error
classification stay identical to the crawler layer:

| Provider | File | Model setting |
|---|---|---|
| Gemini | `src/llm/providers/gemini.py` | `GEMINI_MODEL` |
| Groq | `src/llm/providers/groq.py` | `GROQ_MODEL` |
| DeepSeek (OpenAI-compatible) | `src/llm/providers/deepseek.py` | `DEEPSEEK_MODEL` |

The orchestrator walks `LLM_PROVIDER_ORDER` (default
`gemini,groq,deepseek`). Each provider gets its own bounded retry budget via
the shared `retry_async`; when a provider is exhausted the engine falls
through to the next one. If **every** provider fails, it raises a terminal
`ProviderUnavailableError` — it never returns a partial, empty, or
placeholder result to be mistaken for a successful extraction.

### Deterministic chunking and the 413 path

`src/extraction/chunker.py` splits oversized payloads using a deterministic
`ceil(len(text) / 4)` token estimate against `LLM_TOKEN_BUDGET`. The chunker
guarantees ordering is preserved, nothing is dropped or duplicated, and
concatenating the chunks reproduces the original payload **exactly**.

A `413 PayloadTooLargeError` is deliberately *not* retried as-is — retrying
an oversized payload unchanged just fails again. Instead the orchestrator
recursively halves the payload and re-submits the pieces, so a 413 triggers
the chunking path rather than an infinite retry loop. Recursion bottoms out
at a single character, where the error is re-raised honestly.

### Observability

Every provider attempt persists one `LLMRequest` row recording `model`,
`retry_count`, `latency_ms`, and `fallback_used`, so a fallback chain is
fully reconstructable after the fact — including the attempts that failed,
not just the one that eventually succeeded.

**Note:** the Phase 8 engine is fully built and unit-tested (18 tests across
`test_llm_providers.py`, `test_llm_orchestrator.py`, and `test_chunker.py`),
but is **not yet wired into any CLI vertical** — no vertical currently needs
LLM extraction. It gets consumed by the startups/products pipelines in
Phases 9-11. No live LLM calls have been made, because no API keys are
configured in this environment; provider tests are `respx`-mocked against
each vendor's documented response shape.

## Date engine and freshness (Phase 5)

`src/extraction/dates.py` implements the full priority chain from Section
14: JSON-LD → OpenGraph/meta tags → `<time datetime>` → structured
source-provided value → visible text (absolute, then relative) → an
adapter-supplied heuristic. Everything normalizes to UTC; nothing is ever
guessed — the chain returns `None` if no strategy succeeds.

`is_fresh(timestamp, reference_time, window_hours=24)` is timezone-aware,
takes an explicit reference time (never implicit wall-clock `now()`, so
it's freezable in tests), and treats the 24-hour boundary as inclusive with
a small tolerance for clock skew on `reference_time`-relative future
timestamps.

## Rate-limit (429/403) and payload-size (413) strategy

- `src/crawlers/retry.py` implements bounded exponential backoff with full
  jitter (`compute_backoff_seconds`), honoring an explicit `Retry-After`
  when the server provides one.
- `src/crawlers/http.py` classifies `429` and rate-limit-flavored `403`s
  (GitHub-style) as `RateLimitError` (retried); other `401`/`403`s as
  `BlockedSourceError` (never retried, never bypassed — see Section 31).
- `413` is raised as `PayloadTooLargeError` and is **never retried** as-is —
  the chunker (Phase 8/18) must split the payload first.
- All of this is enforced by `retry_async`, which every network and (later)
  LLM call routes through, so retry semantics are consistent everywhere.

## Deduplication

Enforced at the database level via unique constraints (Section 28), not
only in application code — see `tests/test_models_and_dedup.py` and
`tests/test_repositories.py::test_concurrent_workers_racing_same_url_produce_one_row`,
which proves 10 concurrent "workers" racing on the same URL produce exactly
one row using `INSERT ... ON CONFLICT DO NOTHING`.

## Scaling to 500k+

The worker pool (`src/pipeline/workers.py`) takes `max_concurrency` as a
parameter, sourced from `MAX_CONCURRENCY`/`--workers`. Scaling from a
demo run to 500k+ records is intended to be: more workers, a Redis-backed
job queue in front of `CrawlJob` rows (Phase 9+), a larger Postgres
instance/connection pool, and raw HTML moved to S3/MinIO instead of
Postgres (`RAW_STORAGE_BACKEND=s3` in settings) — not a rewrite of the
crawler logic itself, which is already adapter-agnostic and
concurrency-bounded.

## Ethical / authorized crawling strategy

Tiered, documented per source in `src/config/sources.py`: official API >
RSS/Atom > sitemap > polite HTTP crawl > permitted browser rendering. No
CAPTCHA-solving, no credential bypass, no ignoring `robots.txt`. A blocked
source is recorded (`BlockedSourceError`) and the pipeline moves on — it
does not retry or attempt to defeat the block.

## Next phases

Phase 8 (LLM orchestration) is complete. Immediate priorities, in order:

1. **Replace the dead Papers With Code source** — it now redirects to
   HuggingFace and returns HTML. Swap in HuggingFace Papers or Semantic
   Scholar behind the existing adapter interface.
2. **Phase 9-11** — startups and products pipelines, which are the first
   real consumers of the Phase 8 LLM orchestrator.
3. **Phase 12** — entity resolution (normalization, aliases, fuzzy matching,
   mapping log).
4. **Phase 13-16** — quality/metrics layer, CSV/XLSX/Google Sheets export,
   architecture documentation, and the final requirement-matrix audit.

See `docs/DEVELOPMENT_HANDOFF.md` for the full phase-by-phase log and
`CLAUDE_HANDOFF.md` for the current session checkpoint and exact next task.
