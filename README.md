# GraphOne / FrontierAtlas Intelligence Pipeline

Async, fault-tolerant ingestion pipeline for AI research papers, startups,
products, news, and jobs. Built for the GraphOne / FrontierAtlas AI Engineer
assessment.

## Project status (honest, not aspirational)

This repo is being built in phases (see `docs/DEVELOPMENT_HANDOFF.md` for the
full phase-by-phase log). **Phases 1-5 are implemented and test-verified as
of this commit:**

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo foundation, settings, structured logging, Docker | ✅ Done |
| 2 | Database models (14 tables), repositories, idempotent upserts | ✅ Done |
| 3 | Async crawler core: HTTP client, retry/backoff, worker pool | ✅ Done |
| 4 | Research paper pipeline: arXiv + Papers With Code + GitHub enrichment | ✅ Done |
| 5 | Deterministic date engine + 24h freshness utility | ✅ Done |
| 6-16 | News, jobs, startups, products, LLM orchestration, entity resolution, export, final audit | ⏳ Not yet built |

`python -m src.main --vertical research` **actually runs** end-to-end
(arXiv + Papers With Code discovery → GitHub star enrichment → schema
validation → idempotent Postgres/SQLite persistence). Other verticals still
just report their registered sources — those adapters land in Phases 6-11.

## A critical environment note

This codebase was developed inside a sandboxed environment whose network
egress is restricted to `pypi.org`, `npmjs.org`, and `github.com`/
`api.github.com`. It **cannot** reach arXiv, RSS feeds, job boards, or the
Google Sheets API from inside that sandbox, and has no local Postgres/Redis/
Docker daemon.

Practical implications:
- Everything in Phases 1-5 was **actually run and verified** — 116 pytest
  tests pass (2 more, live GitHub calls, pass when the anonymous rate limit
  isn't already exhausted), including a genuine 10-way concurrent race test,
  full arXiv/Papers With Code adapter + pipeline tests against realistic
  fixtures, and multiple real HTTP calls to `api.github.com` (see below).
- The research pipeline's arXiv and Papers With Code adapters call the real
  official APIs and will work unmodified once run somewhere with normal
  internet egress — confirmed by actually invoking `python -m src.main
  --vertical research` in this sandbox: it correctly received `403`s from
  the egress proxy, classified them as `BlockedSourceError` (not bypassed,
  not retried forever), and reported `valid_records: 0` truthfully instead
  of fabricating anything (Section 48 "No Fake Success"). See the transcript
  in `docs/DEVELOPMENT_HANDOFF.md`.
- GitHub enrichment (`src/extraction/github.py`) was verified against the
  **real** GitHub API multiple times in this sandbox, including pulling
  `karpathy/nanoGPT`'s actual star count.

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

Python 3.12 · asyncio · httpx · Pydantic v2 · SQLAlchemy 2.x (async) ·
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
pytest                      # unit tests only, no live network required (116 tests)
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

Phase 6 (news pipeline: 5 RSS/API sources + freshness) is next, followed by
jobs (Phase 7), then the LLM orchestration engine (Phase 8) which startups/
products enrichment depends on. See `docs/DEVELOPMENT_HANDOFF.md` for the
full phase-by-phase log and exact test results.
