# Development Handoff

Living log of what's actually been built and verified, phase by phase. This
is the source of truth for "is X done" — if it's not in this file as done,
assume it isn't built.

## Completed phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Repo foundation (settings, logging, Docker, source registry) | ✅ Done |
| 2 | Database models (14 tables), repositories, idempotent upserts | ✅ Done |
| 3 | Async crawler core (HTTP client, retry/backoff, worker pool) | ✅ Done |
| 4 | Research paper pipeline (arXiv + Papers With Code + GitHub enrichment) | ✅ Done |
| 5 | Deterministic date engine + 24h freshness utility | ✅ Done |

## Current status

`python -m src.main --vertical research [--target N] [--workers N]` runs
end-to-end: discovers papers from arXiv and Papers With Code, enriches
GitHub star counts for any paper with an explicitly-linked repo, validates
every record against a Pydantic schema, persists raw-document provenance,
and idempotently upserts into `research_papers`. Verified in this sandbox
against `sqlite+aiosqlite:///:memory:` (Postgres wasn't available locally,
but the models are dialect-portable — see `src/storage/models.py`
`PortableJSONB`/`PortableUUID`).

All other verticals (news, jobs, startups, products), the LLM orchestrator,
entity resolution, and export are **not built**. `--vertical news/jobs/
startups/products` reports registered sources only.

## Files changed this phase (Phase 4-5)

New:
- `src/extraction/dates.py` — date engine + `is_fresh()`
- `src/extraction/github.py` — GitHub enrichment client (cached, typed errors)
- `src/crawlers/arxiv.py` — arXiv Atom API adapter
- `src/crawlers/papers_with_code.py` — Papers With Code JSON API adapter
- `src/validation/schemas.py` — `ResearchPaperRecord` Pydantic schema
- `src/pipeline/research.py` — research vertical orchestration
- `tests/test_dates.py` (32 tests)
- `tests/test_github_enrichment.py` (16 tests)
- `tests/test_arxiv_adapter.py` (10 tests)
- `tests/test_papers_with_code_adapter.py` (8 tests)
- `tests/test_validation.py` (11 tests)
- `tests/test_research_pipeline.py` (9 tests, respx-mocked end-to-end)
- `tests/fixtures/arxiv_sample_response.xml`
- `tests/fixtures/papers_with_code_sample_response.json`

Modified:
- `src/storage/repositories.py` — added `RawDocumentRepository` (provenance,
  content-hash deduplicated, race-safe via `INSERT ... ON CONFLICT`), added
  `ResearchPaperRepository.exists()`
- `src/crawlers/http.py` — fixed 403 misclassification (see "Bugs found and
  fixed" below)
- `src/main.py` — wired `--vertical research` to actually run the pipeline
  and create tables on startup; other verticals still report-only
- `pyproject.toml` — removed duplicate pytest config (was conflicting with
  `pytest.ini`)
- `tests/test_http_integration.py` — added a live GitHub-enrichment-client
  integration test; capped `max_retries=1` on the existing one so a real
  rate-limit response doesn't make the test hang waiting out a long
  `Retry-After`
- `README.md` — updated project status table and phase 4-5 documentation

## Test results (exact)

```
$ pytest -m "not integration" -q
........................................................................ [ 62%]
............................................                             [100%]
116 passed, 2 deselected in 4.19s

$ pytest -q                      # includes the 2 integration tests
..............................................................ss........ [ 61%]
..............................................                           [100%]
116 passed, 2 skipped in 4.39s
```

The 2 integration tests are marked `skipped`, not failed — they hit the
real GitHub API and correctly self-skip when GitHub's anonymous rate limit
(60 req/hour, shared across this whole session's live checks) is exhausted.
Both have passed earlier in this session; see "Live tests" below.

Breakdown by file (non-integration):

| File | Tests |
|---|---|
| test_arxiv_adapter.py | 10 |
| test_dates.py | 32 |
| test_github_enrichment.py | 16 |
| test_http_client.py | 4 |
| test_models_and_dedup.py | 5 |
| test_papers_with_code_adapter.py | 8 |
| test_repositories.py | 2 |
| test_research_pipeline.py | 9 |
| test_retry.py | 6 |
| test_url_normalization.py | 8 |
| test_validation.py | 11 |
| test_worker_pool.py | 5 |
| **Total** | **116** |

## Live tests (real network calls, no mocks)

Run explicitly with `pytest -m integration`. Both passed earlier this
session against the real GitHub API:

1. `test_fetches_real_github_api_response` — `GET
   api.github.com/repos/pytorch/pytorch` → 200, verified response contains
   "pytorch" and a real sha256 content hash.
2. `test_github_enrichment_client_against_real_api` — pulled
   `karpathy/nanoGPT`'s **real** star count (62,005 at time of writing)
   through the actual `GitHubEnrichmentClient` used by the research
   pipeline, not a separate code path.

Additionally, `python -m src.main --vertical research --target 2` was run
directly against this sandbox's egress-restricted network (see README). It
correctly received `403 Forbidden` from the network proxy for both
`export.arxiv.org` and `paperswithcode.com`, classified each as
`BlockedSourceError` (not bypassed, not retried indefinitely), and reported
`valid_records: 0` — proving the "reject rather than fabricate" behavior
end-to-end under a real failure condition, not just in a mocked test.

## Mocked tests (no live network)

All 116 tests in the default suite. Adapter tests use realistic fixtures
matching each API's documented response schema (`tests/fixtures/`) rather
than live calls, per Section 38's requirement that the default suite never
depend on external sites. HTTP-classification and rate-limit edge cases use
`httpx.MockTransport`; full pipeline runs use `respx` to mock all three
real endpoints (`export.arxiv.org`, `paperswithcode.com`,
`api.github.com`) simultaneously and verify the whole discover → validate →
enrich → persist chain, including a genuine SQLite-backed dedup check
across two full pipeline runs.

## A bug found and fixed this phase

**Repo-level 403 handling.** `src/crawlers/http.py` originally treated every
`403` response as a permanent `BlockedSourceError`. The live GitHub
integration test surfaced that GitHub actually returns `403` (not `429`)
for anonymous rate-limit exhaustion, signaled via `X-RateLimit-Remaining:
0`. Fixed to check that header and raise `RateLimitError` (retryable, with
`Retry-After` computed from `X-RateLimit-Reset`) in that specific case,
while any other `403` still raises `BlockedSourceError`. Locked in with 3
new mocked tests in `tests/test_http_client.py` and used correctly by
`GitHubEnrichmentClient` (`tests/test_github_enrichment.py::
test_rate_limit_403_propagates_as_rate_limit_error`) and the pipeline
(`tests/test_research_pipeline.py::
test_pipeline_github_rate_limit_leaves_stars_null_not_fabricated`).

A second, smaller finding: the initial live-test `max_retries` value
(2) combined with a real `Retry-After`/`X-RateLimit-Reset` far in the
future caused the test to hang past the sandbox's command timeout instead
of failing fast. Fixed by capping the integration test to `max_retries=1`
(single attempt, no wait) — a testing/harness fix, not a pipeline bug: the
retry logic itself was behaving correctly (honoring the server's stated
reset time), it just isn't the right shape for a synchronous test run.

## Known limitations

- **This sandbox cannot reach `export.arxiv.org` or `paperswithcode.com`**
  (network egress restricted to pypi/npm/github). Both adapters are built
  against each API's real, documented schema and tested against fixtures
  that match that schema, but neither has been exercised against live
  arXiv/PWC traffic from this environment — only from a real deployment
  (or by construction, since the fixtures mirror the documented API shape).
- GitHub enrichment is rate-limited to 60 requests/hour without a
  `GITHUB_TOKEN`; set one in `.env` for real runs at scale.
- No Alembic migrations yet — `Base.metadata.create_all` is used for schema
  setup, fine for the assessment demo but not a real migration story.
- `ArxivAdapter`'s GitHub-URL-in-abstract extraction is a regex over free
  text; it will miss unconventional phrasing (e.g. a repo mentioned without
  "github.com" verbatim, or split across lines in a way that breaks the
  match) — this is a recall limitation, not a fabrication risk, since a
  miss just means `github_url: null`.
- `PapersWithCodeAdapter`'s pagination assumes the API's `items_per_page`
  cap of 50 holds; if PWC changes that limit the adapter would need a
  corresponding update (would fail loudly via unexpected page counts, not
  silently under-fetch).
- The research pipeline's `per_adapter_target` split (target // 2) is a
  simple even split; it doesn't yet account for one source running dry
  before the other reaches its share.

## Remaining work (not started)

Phases 6-16 per the master prompt: news pipeline (5 sources), jobs pipeline
(5 sources), LLM orchestration (Gemini/Groq/DeepSeek fallback + chunking +
rate limiting), entity resolution (normalization/aliases/fuzzy matching +
mapping log), startups pipeline, products pipeline, quality/metrics layer,
CSV/XLSX/Google Sheets export, full documentation (architecture.pdf), and
the final requirement-matrix audit.

## Environment variables used by Phase 4-5

From `.env.example` (see that file for the full list):

- `DATABASE_URL` — required to run `--vertical research` for real (defaults
  to a local Postgres URL; override to `sqlite+aiosqlite:///path/to.db` or
  `sqlite+aiosqlite:///:memory:` for a quick local check without Postgres)
- `GITHUB_TOKEN` — optional but strongly recommended; raises the GitHub API
  rate limit from 60/hour to 5,000/hour
- `MAX_CONCURRENCY`, `MAX_RETRIES`, `REQUEST_TIMEOUT_SECONDS`,
  `RETRY_BASE_DELAY_SECONDS`, `RETRY_MAX_DELAY_SECONDS` — crawler tuning
- `FRESHNESS_WINDOW_HOURS` — defaults to 24, used by `is_fresh()` (not yet
  called from the research pipeline itself, since research papers aren't
  freshness-gated the way news/jobs are — it's exercised directly in
  `tests/test_dates.py` and will be wired into news/jobs in Phase 6-7)

## Commands to run the research pipeline

```bash
# Real run against Postgres (requires network egress to arxiv.org / paperswithcode.com / github.com)
cp .env.example .env
# edit .env: set DATABASE_URL to your Postgres instance, optionally GITHUB_TOKEN
pip install -r requirements.txt
docker compose up -d postgres
python -m src.main --vertical research --target 1000 --workers 20

# Quick local check without Postgres (uses an in-memory SQLite DB)
DATABASE_URL="sqlite+aiosqlite:///:memory:" python -m src.main --vertical research --target 20

# Run just the research pipeline's own test suite
pytest tests/test_arxiv_adapter.py tests/test_papers_with_code_adapter.py \
       tests/test_github_enrichment.py tests/test_validation.py \
       tests/test_research_pipeline.py tests/test_dates.py -v

# Full suite
pytest                      # 116 passed
pytest -m integration       # 2 more, live GitHub calls (self-skip if rate-limited)
```
