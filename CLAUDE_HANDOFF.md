# Claude Handoff

Operational checkpoint for resuming work on the GraphOne / FrontierAtlas
intelligence pipeline. Keep this file short and factual.

**Last updated:** 2026-09-09 (session 5)
**Branch:** `phase-8-repair`
**Repo:** https://github.com/a1creator284/graphone-intelligence-pipeline

## Current Status

Phases 1-8, Phase 12 (entity resolution), the OpenAlex research source, and
**the startups vertical** are implemented and green: **366 passed, 2
deselected** (the 2 deselected are `-m integration`, live-network, opt-in).
Baseline at session start was 321; session 5 added 45 tests and broke none.

**Session 5 did exactly one thing: implemented the STARTUPS ingestion
milestone (requirement #1). See below.**

## What Session 5 Did

### Milestone: startups vertical (YC company directory)

**Source verification came first.** Probes actually executed before any
code was written:

| Probe | Result |
|---|---|
| YC Algolia with the key recorded in session 3's notes | **403** `Invalid Application-ID or API key` — stale key, confirming the old blocker |
| `ycombinator.com/companies` page source | **200**; contains `window.AlgoliaOpts = {"app":"45BWZJ1SGC","key":"NzllNTY5..."}` |
| YC Algolia with that live key | **200**, real hits |
| `tags:Artificial Intelligence` facet | **1,001** companies |
| AI tag group (AI ∪ Artificial Intelligence ∪ Generative AI ∪ Machine Learning) | **1,937** companies across **38** batches, largest batch 162 |
| `yc-oss.github.io/api` (third-party YC mirror) | 200, 6,204 companies — **not used**; a first-party endpoint was available |

So the previously-recorded "YC 403" was a **stale credential, not a blocked
source**. The endpoint used is the same public, search-only Algolia index
that YC's own directory UI queries — no login, no HTML scraping, no access
control bypassed. The key is scoped by Algolia to the `ycdc_public` tag
filter, so it can only read what the public directory already shows.

### Implementation

- `src/crawlers/ycombinator_startups.py` — `YCombinatorStartupsAdapter`,
  the same `SourceAdapter` discover/fetch/parse interface as arXiv/OpenAlex.
  Reuses `AsyncHttpClient`, `run_adapter`, the existing repositories, and
  `EntityResolver`. No new infrastructure.
- **Real pagination, and the reason for its shape.** Algolia caps *any
  single query* at 1,000 retrievable hits (`paginationLimitedTo`), and the
  AI slice is 1,937. Rather than silently truncating at 1,000, discovery
  **partitions the index by YC batch** — one facet query returns every batch
  and its count, then each batch is paged independently with `page` /
  `hitsPerPage`. No batch is near the cap, so the same code reaches the full
  tagged population **without a code change**.
- **Deterministic.** Partition order is count-desc with the batch name as
  tie-break, so two runs with the same target crawl the same pages in the
  same order. Dedup is on the canonical YC company URL: in-page in the
  adapter, cross-page in the pipeline, and enforced for real by the
  `uq_startups_source_url` constraint.
- **Graceful degradation.** If the facet query fails or returns an
  unusable body, discovery falls back to a single unpartitioned crawl
  *bounded by the 1,000-hit cap* — it never issues a page request the API
  would reject, and it never invents batch names.
- `src/pipeline/startups.py` — `run_startups_pipeline`, same shape as
  research/jobs: worker pool → `validate_startup_record` → `RawDocument`
  provenance → `EntityResolver` → `StartupRepository.upsert`. Fill-forward
  target allocation is already N-adapter-general, so adding a second
  startups source is a one-line append to `ADAPTER_CLASSES`.
- `src/validation/schemas.py` — `StartupRecord` + `validate_startup_record`.
- `src/config/sources.py` — the `ycombinator_directory` entry updated to
  `OFFICIAL_API` with the real parsing strategy and the real limitations
  (1,000-hit cap, browser-visible rotatable key, `team_size` nullability).
- `src/config/settings.py` — optional `yc_algolia_app_id` /
  `yc_algolia_api_key`, so a YC key rotation needs config, not a code edit.
- `src/main.py` — `--vertical startups` wired through `engine_scope()`
  (Known Issue #1's guidance followed; the CLI exits 0 on its own).

### Anti-fabrication discipline

- `entity_name` ← `name`, `employee_count` ← `team_size`, `source_url`
  built from the record's **own `slug`**. Those are the only three fields
  emitted — the adapter's `data` dict is asserted to contain exactly
  `{entity_name, employee_count}`.
- **No founders, funding, valuations, descriptions, or founding dates are
  produced at all** — the Startup table does not store them and nothing is
  synthesized. A test asserts those keys never appear.
- Missing/blank/non-integer `team_size` → **NULL**, never `0`, never
  estimated from the batch.
- A company with no `slug` is **skipped**, even though its name is present
  and a URL could plausibly be guessed from it.
- An Algolia error body raises `ParsingError` rather than looking like an
  empty page — a broken source cannot masquerade as a quiet one.
- The target is a **ceiling, not a quota**: a shortfall logs
  `startups_target_not_met` and reports the true count.

### Tests

```
$ .venv/bin/python -m pytest tests/test_yc_startups_adapter.py tests/test_startups_pipeline.py -q
45 passed

$ .venv/bin/python -m pytest -q
366 passed, 2 deselected, 31 warnings in 11.16s
```

45 new tests (35 adapter + 10 pipeline) covering: parsing and verbatim
field mapping; missing `team_size` (null / absent key / non-numeric) staying
NULL; missing slug and blank name being skipped; forbidden fabricated fields
absent; malformed JSON, HTML, the **real** Algolia 403 body, non-list
`hits`, JSON-array payload, and non-dict entries; in-page duplicates,
stable cross-page dedup keys, trailing-slash slug normalization; provenance
(`fetch_result` attached, `source_url` is the public YC page); batch
partitioning against the **real** facet listing, page splitting, target
never exceeded, deterministic ordering, the >1,000 path, both fallback
paths, credential override, page-size clamp; registry consistency; and
end-to-end persistence, provenance rows, cross-run dedup, entity
resolution, honest shortfall, and rejection-not-repair.

**Fixtures are real.** All three were captured live from the YC Algolia
endpoint on 2026-09-09 with the exact queries recorded in the test module
docstring (a 5-hit page, the real 38-batch facet listing, and the real 403
error body). Where a test needed a shape the live API did not hand us (null
`team_size`, missing `slug`), it *derives* it by editing a key of the
captured response and says so. No invented company metadata.

### Live verification (this session)

```
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" \
    .venv/bin/python -m src.main --vertical startups --target 8

facet query      -> 200, 1714 bytes
Summer 2024 page -> 200, 24925 bytes
adapter_run_complete: discovered=1 fetched=1 parsed_records=8
startups_pipeline_complete: target=8 valid_records=8 duplicates=0
  rejected=0 entities_resolved=8 entities_created=8
CLI_EXIT=0
```

The 8 persisted rows, with their provenance URLs:

```
DreamRP     emp=2   https://www.ycombinator.com/companies/dreamrp
Conductor   emp=8   https://www.ycombinator.com/companies/conductor
Syntra      emp=10  https://www.ycombinator.com/companies/syntra
PathPilot   emp=3   https://www.ycombinator.com/companies/pathpilot
Merlin AI   emp=19  https://www.ycombinator.com/companies/merlin-ai
Freestyle   emp=5   https://www.ycombinator.com/companies/freestyle
Arva AI     emp=20  https://www.ycombinator.com/companies/arva-ai
Affil.ai    emp=4   https://www.ycombinator.com/companies/affil-ai
```

Spot-checked provenance URLs (`dreamrp`, `arva-ai`, `affil-ai`) all return
**200** on ycombinator.com. Every value is a real YC directory record.

### Is the 1,000-record requirement genuinely supported?

**Supported and source-verified; the full run has not been executed.**

- The source **live-reports 1,937 AI-tagged companies** across 38 batches
  (`nbHits` from the facet query — YC's own count, not an estimate).
- Batch partitioning is what makes >1,000 reachable, and it is proven by
  test: a `max_results=1500` discovery emits exactly 1,500 hits across
  multiple batches with **no single query exceeding the 1,000-hit cap**.
- Per the session brief, the full 1,000-record ingestion was **not** run —
  only the 8-record live smoke test above. So 1,000 rows are **not yet
  physically in a database**; what is verified is that the source holds
  ~1,937 real records and the code paginates to them without modification.
  `--vertical startups --target 1000` is the command; expect ~13 page
  requests.

### Session 5 files changed

**New:** `src/crawlers/ycombinator_startups.py`,
`src/pipeline/startups.py`, `tests/test_yc_startups_adapter.py`,
`tests/test_startups_pipeline.py`, and three real captured fixtures
(`tests/fixtures/yc_algolia_{sample_response,batch_facets,error_response}.json`).

**Modified:** `src/config/settings.py` (YC key overrides),
`src/config/sources.py` (registry entry corrected),
`src/validation/schemas.py` (`StartupRecord`), `src/main.py`
(`--vertical startups`).

Not touched: every other pipeline, adapter, and `src/resolution/*`. No
existing test deleted or weakened.

## What Session 4 Did

**Session 4 fixed the CLI/process-exit hang (previously Known Issue #1).
It is resolved — see below.**

Session 3 implemented one milestone: replacing the dead Papers With Code
research source with OpenAlex, plus a fill-forward fix to the research
target allocation.

## What This Session Did

### Session 4 — CLI/process-exit hang fixed (one task only)

**Symptom.** `python -m src.main --vertical research` finished its work and
logged its summary, then the process never exited (`timeout` → exit 124).
Reproduced on the pre-OpenAlex baseline, so it was pre-existing.

**Root cause (measured, not guessed).** `init_engine()` creates a
*process-global* `AsyncEngine`, and `src/main.py` never disposed it. The
undisposed pool keeps its connections checked out, and under aiosqlite the
pool owns a **non-daemon** `_connection_worker_thread`. A non-daemon thread
blocks interpreter shutdown after `asyncio.run()` returns. Confirmed by
enumerating live threads after `asyncio.run()`:
`[('Thread-1 (_connection_worker_thread)', daemon=False)]`.

**Fix — at the engine-ownership boundary, in `src/storage/database.py`:**
- `dispose_engine()` — awaits `engine.dispose()` and clears the
  `_engine` / `_session_factory` globals. Idempotent; a no-op if no engine
  was ever created.
- `engine_scope(database_url=None)` — async context manager that owns the
  engine for the duration of a run and disposes it in a `finally`, so the
  pool is released on the failure path too.
- `src/main.py`: `_run_research` / `_run_news` / `_run_jobs` now acquire
  their engine via `async with engine_scope() as engine:` instead of a bare
  `init_engine()` they never tore down. `main()` is unchanged.

`AsyncHttpClient` was already correctly closed by `async with` inside
`run_research_pipeline`; it was **not** the leak, and was left alone.

**No** timeouts, `sys.exit` hacks, daemon-thread tricks, or swallowed
exceptions were used. Nothing else was refactored.

### Verification

```
# before
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" timeout 45 \
    python -m src.main --vertical research --target 1
EXIT=124        (work done in ~2s, then hung until killed)

# after
EXIT=0          real 0m1.4s
```

Live research run, both sources, still correct and now self-terminating:

```
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" OPENALEX_MAILTO=... \
    python -m src.main --vertical research --target 6
valid_records: 6  duplicates: 0  rejected: 0
by_source: {"arxiv": 3, "openalex": 3}
CLI_EXIT=0      real 0m1.7s
```

### Session 4 files changed

- `src/storage/database.py` — added `dispose_engine()`, `engine_scope()`
- `src/main.py` — three vertical runners use `engine_scope()`
- `tests/test_cli_lifecycle.py` — **new**, 6 regression tests: pool worker
  thread is retired when the scope exits; pool is still disposed when the
  run body raises (and the error propagates); globals are reset; second
  `dispose_engine()` call is safe; a source guard so a future edit can't
  reintroduce a bare `init_engine()` in the CLI; and an end-to-end
  `subprocess` test that the CLI process terminates on its own.
- `tests/test_cli_integration.py` — the news test patched
  `src.main.init_engine`; it now patches `src.main.engine_scope` with a
  dummy async context manager. Every original assertion is unchanged.

```
$ .venv/bin/python -m pytest tests/test_cli_lifecycle.py tests/test_cli_integration.py -q
7 passed

$ .venv/bin/python -m pytest -q
321 passed, 2 deselected, 31 warnings in 7.31s
```

## What Session 3 Did

### Milestone: OpenAlex replaces Papers With Code

- `src/crawlers/openalex.py` — `OpenAlexAdapter`, same `SourceAdapter`
  discover/fetch/parse interface as `ArxivAdapter`. Official REST API
  (`api.openalex.org/works`), filtered to concept `C154945302`
  ("Artificial intelligence"), sorted `publication_date:desc`,
  `page`/`per-page` pagination, `select=` limited to the fields actually
  consumed. Basic paging stops at the API's documented 10,000-result cap
  rather than issuing requests that would 400. `per-page` clamped to 200.
- **Polite pool:** `mailto=` is appended only when `OPENALEX_MAILTO` is set
  (new optional setting `Settings.openalex_mailto`, documented in
  `.env.example`). Unset → the parameter is omitted; no address is invented.
- **Field mapping is verbatim only.** `title` (falling back to
  `display_name`, which OpenAlex returns as the same value),
  `authorships[].author.display_name`, `publication_date`, and the `W…` work
  ID as `paper_external_id`. `paper_url` is chosen from URLs *present in the
  response*, in order `doi` → `primary_location.landing_page_url` → the
  OpenAlex work URL — never constructed from parts.
- **`github_url`/`github_stars` are always NULL from this source.** OpenAlex
  exposes no repository relation, so nothing is inferred. `github_stars`
  remains the exclusive output of `GitHubEnrichmentClient`.
- **Absent fields stay absent.** No `publication_date` → `published_date` is
  `None`. No `authorships` → `[]`. No title or no usable URL → the record is
  skipped, not repaired.
- **A source failure yields zero records.** A JSON error body (OpenAlex's
  real 400 shape has `error`/`message` and no `results`), an HTML page, or a
  non-list `results` all raise `ParsingError`. An error page is never
  silently treated as an empty-but-valid page.

### Registry / wiring

- `src/config/sources.py`: new `openalex` entry (`Vertical.RESEARCH`,
  `OFFICIAL_API`). `papers_with_code` set to `enabled=False` with the
  breakage recorded in `known_limitations` — **disabled, not deleted**, so
  the dead-source evidence and history survive.
- `src/pipeline/research.py`: `ADAPTER_CLASSES = [ArxivAdapter,
  OpenAlexAdapter]`. The PWC adapter module and its own test file are
  untouched and still pass.
- `ArxivAdapter` was **not** modified. No compatibility issue was found.

### Fill-forward target allocation

The old `per_adapter_target = max(1, target // len(ADAPTER_CLASSES))` split
silently missed the global target whenever one source ran dry. Replaced with
a minimal two-pass scheme in `run_research_pipeline` (no pipeline redesign):

1. **Pass 1** — each adapter is asked for a fair share of what is *still
   outstanding* (`ceil(remaining / adapters_left)`), so an earlier source's
   shortfall is carried forward to later ones. Loop breaks early once the
   target is met, so no over-collection.
2. **Pass 2** — one bounded extra round against adapters that were *not*
   exhausted in pass 1 (`produced >= asked_for`), with the ceiling raised by
   the shortfall. Adapters that ran dry are skipped rather than re-fetched.
3. Records are deduped by `source_url` across passes, so a retry cannot
   inflate counts. If the target is still unmet, a `research_target_not_met`
   warning is logged and the real count is reported. **Nothing is fabricated
   to reach the target.**

## Files Changed

**New**
- `src/crawlers/openalex.py`
- `tests/test_openalex_adapter.py` (25 tests)
- `tests/fixtures/openalex_sample_response.json` (real captured response)
- `tests/fixtures/openalex_sample_response_no_doi.json` (real captured
  response, `has_doi:false`, for genuinely-null optional fields)

**Modified**
- `src/config/settings.py` — added optional `openalex_mailto`
- `src/config/sources.py` — added `openalex`; `papers_with_code`
  `enabled=False` + breakage note
- `src/pipeline/research.py` — OpenAlex wired in; fill-forward allocation
- `tests/test_research_pipeline.py` — PWC mocks → OpenAlex mocks (intent of
  every existing test preserved), plus 4 new fill-forward tests
- `.env.example` — `OPENALEX_MAILTO`
- `README.md` — targeted edits only (PWC section now records the
  replacement, adapter list, source tree, next-phases item closed)
- `CLAUDE_HANDOFF.md` — this file

Not touched: `src/crawlers/arxiv.py`, `src/crawlers/papers_with_code.py`,
`src/resolution/*`, every other pipeline. No existing test deleted.

## Tests

```
$ .venv/bin/python -m pytest -q
315 passed, 2 deselected, 31 warnings in 7.99s
```

Baseline before the change was `286 passed, 2 deselected`.

```
$ .venv/bin/python -m pytest tests/test_openalex_adapter.py -q
25 passed in 0.69s

$ .venv/bin/python -m pytest tests/test_research_pipeline.py -q
13 passed in 2.17s
```

**Fixtures are real.** Both OpenAlex fixtures were captured live from
`api.openalex.org` on 2026-09-09 with the exact query the adapter issues
(recorded verbatim in the test module docstring). Where a test needed a
shape the live API did not hand us (a work with no `publication_date`, a
work with no usable URL), it *derives* it by deleting a key from the
captured response and says so in the docstring. No invented paper metadata
is presented as a real fixture. The error-body test uses the real 400
payload shape captured from `/works?filter=badfilter:1`.

Coverage: successful parse, required-field mapping, missing optional fields
(null `doi`, missing date, missing authorships), skip-on-missing-required,
malformed JSON, HTML error page, OpenAlex error body, non-list `results`,
pagination (page splitting, remainder page, 10k cap, per-page clamp),
source URL/provenance, `mailto` on/off, registry consistency, PWC disabled,
and 4 fill-forward tests (shortfall carried forward, bounded retry of a
non-exhausted adapter, all-sources-dry → zero records, no extra requests
once the target is met).

## Known Issues

1. ~~**`python -m src.main` does not exit cleanly after a run.**~~
   **FIXED in session 4** — the global async engine is now disposed via
   `engine_scope()`. The CLI exits 0 on its own. Remaining caveat: any
   *future* entrypoint (a script, worker, or new vertical) that calls
   `init_engine()` directly will reintroduce the same hang — use
   `engine_scope()`, or pair `init_engine()` with `dispose_engine()`. The
   source guard in `tests/test_cli_lifecycle.py` covers `src/main.py` only.
2. **OpenAlex `publication_date` is a date only** (no time) and is
   occasionally a publisher-supplied *future* date (the captured fixture
   contains 2045-12-10 and 2041-01-01 — those are genuinely what the API
   returned). Research records have no 24h freshness gate, so this does not
   currently reject anything, but a future freshness rule on research would
   need to account for it.
3. **OpenAlex basic paging caps at 10,000 results.** Reaching 1,000 papers
   is fine; going deeper would need cursor paging (`cursor=*` +
   `meta.next_cursor`, both confirmed present in live responses).
4. **The full 1,000-paper run has not been attempted** this session — only a
   6-record live smoke test, per the session brief.
5. ~~Startups vertical does not exist (YC Algolia → 403).~~ **DONE in
   session 5** — the 403 was a stale key; the live key from the page source
   works. Remaining startups caveats: (a) the public Algolia key is
   browser-visible and YC may rotate it — override with `YC_ALGOLIA_API_KEY`
   if a run starts 403ing; (b) a single Algolia query can retrieve at most
   1,000 hits, which is why discovery partitions by batch — a future batch
   exceeding 1,000 AI companies would need a further sub-partition (e.g. by
   industry facet); (c) the AI slice is ~1,937, so 1,000 is reachable but
   there is no large headroom; (d) **the full 1,000-record run has not been
   executed** — only an 8-record live smoke test. The **products** vertical
   still does not exist (Product Hunt GraphQL → OAuth); re-verify before
   building.
6. No export module (`src/export/` still empty); requirement #13 unmet.
7. Phase 8 has never made a live LLM call (no API keys) and still has no
   production caller.
8. Entity resolution is wired into jobs **and startups** (by design —
   research/news have no company field).
9. No `architecture.pdf` (requirement #15). No Alembic migrations.
10. 31 test warnings (newspaper3k / bs4 / pytest-asyncio deprecations).
    Cosmetic.

## Live Verification (this session)

Network egress is open. All probes were actually executed.

```
$ curl -o /dev/null -w "%{http_code}" \
  "https://api.openalex.org/works?filter=concepts.id:C154945302&per-page=2&mailto=..."
200   (content-type: application/json)
```

Adapter-level smoke test through the real `AsyncHttpClient`:

```
GET https://api.openalex.org/works?filter=concepts.id:C154945302&sort=publication_date:desc
    &per-page=3&page=1&select=...&mailto=graphone-pipeline@example.com
status 200  bytes 6423  parsed 3
 valid: True  {"title": "Artificial Intelligence in Plant Sciences",
               "authors": ["Dr. Nupur Prasad"],
               "paper_url": "https://doi.org/10.5281/zenodo.17036033",
               "paper_external_id": "W7166029900",
               "github_url": null, "github_stars": null,
               "published_date": "2045-12-10 00:00:00+00:00"}
TOTAL 3
```

All 3 live records passed `validate_research_paper` unmodified, and all 3
had `github_url`/`github_stars` null — confirming nothing is fabricated.

Full pipeline live run (arXiv + OpenAlex together):

```
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" OPENALEX_MAILTO=... \
  python -m src.main --vertical research --target 6

registered_sources: ["arxiv", "openalex"]
arxiv    -> status 200, parsed 3, asked_for 3, new 3
openalex -> status 200, parsed 3, asked_for 3, new 3
research_pipeline_complete: target=6 discovered=2 valid_records=6
  duplicates=0 rejected=0 github_enriched=0
  by_source={"arxiv": 3, "openalex": 3}
```

Target met exactly, from both sources, with no fabricated rows. (The process
hung on exit at the time; that hang is **fixed as of session 4** and the same
command now exits 0 — see "Session 4" above.)

## Remaining Assessment Gaps

| Req | Item | Status |
|---|---|---|
| 1 | 1,000 startups | ⚠️ pipeline live (YC directory, ~1,937 real records available); full 1,000 run not yet executed |
| 2 | 1,000 products | ❌ no pipeline |
| 3 | 1,000 papers | ⚠️ 2 live sources (arXiv + OpenAlex); full 1,000 run not yet attempted |
| 4 | ≥5 news sources | ✅ 5 configured |
| 5 | ≥5 job sources | ✅ 5 configured |
| 6 | 24h freshness | ✅ enforced + live-proven |
| 7 | Gemini→Groq→DeepSeek | ✅ built, ❌ never called live |
| 8 | 413 chunking | ✅ |
| 9 | 429 backoff+jitter | ✅ |
| 10 | Entity resolution | ✅ (jobs + startups) |
| 11 | Provenance | ✅ for the 4 live verticals |
| 12 | Anti-bot strategy | ✅ documented tiering |
| 13 | Six-tab export | ❌ not built |
| 14 | README | ✅ |
| 15 | architecture.pdf | ❌ not built |
| 16 | 500k+ scale story | ✅ documented |

## Next Task

**Either** the real 1,000-record startups run (`--vertical startups
--target 1000`, ~13 page requests, source verified to hold ~1,937) to close
requirement #1 end-to-end, **or** Phase 14 below.

**Phase 14: the six-tab export module** (`src/export/`, requirement #13).
(The CLI exit hang that session 3 flagged as a prerequisite is now fixed, so
a long 1,000-paper run will terminate cleanly.)

It is the largest remaining unmet requirement, it is fully self-contained,
it needs no network access, and all six backing tables already exist and are
populated for the three live verticals (research / news / jobs), including
`entity_mapping_log` from Phase 12. Low risk, high assessment value.

Second choice if export is deferred: a real 1,000-paper research run to
prove requirement #3 end-to-end (now plausible with two live sources and
fill-forward allocation). Budget for arXiv's ~1 req/3s politeness ask — it
will take several minutes, and the CLI's non-exit bug (Known Issue #1)
should be fixed first so the run terminates cleanly.

## Important Decisions

- **Disabled Papers With Code instead of deleting it.** The adapter, its
  tests, and its registry entry remain; only `enabled` flipped to `False`
  with the breakage documented. Deleting it would erase the evidence that
  the pipeline handles a dead upstream correctly.
- **Chose OpenAlex over Semantic Scholar / HuggingFace Papers.** OpenAlex
  answered 200 with no key; Semantic Scholar 429s unauthenticated. Verified
  live, not assumed.
- **`paper_url` is selected, never constructed.** DOI → landing page →
  OpenAlex work URL, all values literally present in the response. A work
  with none of the three is dropped rather than given a synthesized URL.
- **Treated a missing `results` key as a hard `ParsingError`.** Returning
  `[]` for an error body would let a broken source look like a quiet source
  — precisely how a dead PWC could have gone unnoticed.
- **Fill-forward is two bounded passes, not a scheduler.** A general
  work-stealing collector would be a real redesign; two passes plus URL
  dedup fixes the actual defect (target missed when a source runs dry) with
  ~40 lines and no change to the adapter interface.
- **Retries only adapters that were not exhausted.** Re-asking a dry source
  just re-downloads the same pages. The retry ceiling is the original ask
  plus the shortfall, and dedup guarantees a retry can never inflate counts.
- **(S5) Used YC's own public Algolia index over the `yc-oss` mirror.**
  Both answered 200, but the Algolia index is first-party and is what the
  YC directory itself renders from; a third-party mirror adds a staleness
  hop for no benefit. Preference for authoritative sources, per the brief.
- **(S5) Partitioned discovery by batch instead of accepting truncation.**
  Algolia's hard 1,000-hit-per-query cap would have silently capped the
  vertical below the requirement. Partitioning by an existing facet costs
  one extra request and makes 1,000+ reachable with no code change.
- **(S5) `source_url` is built from the record's own `slug`, and a company
  without one is dropped.** The slug is a first-party identifier and the
  exact URL YC links to — not a URL guessed from the company name.
- **(S5) Stored only the three fields the Startup table defines.** The API
  returns descriptions, locations, and industries; emitting them would
  invite schema drift, and none are required. Founders/funding are not in
  the response at all and are therefore never produced.
- **Kept `arxiv.py` byte-identical.** No compatibility issue surfaced, and
  the brief said not to touch it otherwise.
- **Rewrote the existing research-pipeline test mocks rather than adding a
  parallel set.** The tests assert pipeline behaviour, not PWC specifically;
  every original assertion's intent is preserved, with counts updated for
  the new fixture (notably `github_enriched` drops 2 → 1, because OpenAlex
  contributes no repo links at all).

## Environment / Dependencies

- Python 3.13.14; project targets >=3.11
- Virtualenv at `.venv/` (gitignored) — **must be recreated on a fresh sandbox**
- All deps from `requirements.txt` install cleanly; `rapidfuzz==3.10.1`
  (already pinned) is what Phase 12 uses
- No Postgres/Redis/Docker daemon locally — use
  `DATABASE_URL="sqlite+aiosqlite:///:memory:"`
- Network egress is **open**
- No LLM API keys configured

## Blockers

None blocking. Startups source access is **resolved** (live-verified this
session). **Products** (Product Hunt GraphQL → OAuth) remains the only
genuinely uncertain source area and needs re-probing before that phase.

## Continue With

```bash
cd /home/user/webapp

# Recreate the environment (the venv is gitignored)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Confirm the baseline is still green (expect: 321 passed, 2 deselected)
.venv/bin/python -m pytest -q

# Re-probe the live research sources
curl -s -o /dev/null -w "openalex: %{http_code}\n" \
  "https://api.openalex.org/works?filter=concepts.id:C154945302&per-page=2"

# Live smoke test (exits cleanly now -- the old non-exit bug is fixed)
DATABASE_URL="sqlite+aiosqlite:///:memory:" OPENALEX_MAILTO="you@example.com" \
  .venv/bin/python -m src.main --vertical research --target 6

# Live startups smoke test (8 real YC records, exits 0)
DATABASE_URL="sqlite+aiosqlite:///:memory:" \
  .venv/bin/python -m src.main --vertical startups --target 8

# Then either the full startups run:
#   ... --vertical startups --target 1000
# or start src/export/ (six-tab export, requirement #13)
```
