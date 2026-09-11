# Final submission audit

## Artifact recovery run — 2026-09-11 UTC (supersedes the blocker below)

The previous session's sandbox was lost with its SQLite DB and workbook. This
session **re-ran the existing, unmodified pipeline** from branch
`genspark_ai_developer` at `0f15acd` into a fresh ignored DB at
`.local/submission.db`, then ran the existing six-tab exporter. No pipeline
code, freshness rule or source implementation was changed, and no record was
fabricated or padded.

### Observed counts (export cutoff `2026-09-11T09:34:44.953842+00:00` UTC)

| Sheet | DB rows | Exported rows |
|---|---:|---:|
| Startups | 1000 | 1000 |
| Products | 1000 | 1000 |
| Research Papers | 1000 | 1000 |
| Jobs | 0 | 0 |
| News | 45 | 45 |
| Entity Mapping Log | 2000 | 2000 |

Per-source detail: Startups `ycombinator_directory` 1000. Products
`huggingface_spaces` 340, `huggingface_models` 349, `openrouter_models` 311
(34 cross-source duplicates suppressed). Research `arxiv` 500, `openalex` 500.
Jobs — all five sources ran (`remoteok_ai_jobs`, `workingnomads_ai_jobs`,
`ycombinator_hn_whoishiring`, `wellfound_ai_jobs`, `builtin_ai_jobs`), 23
discovery units, **0** genuine postings inside the strict 24h window (one
sitemap source was blocked and logged, not substituted). News — all five
sources persisted rows: `hackernews_ai` 19, `techcrunch_ai_rss` 9,
`theverge_ai_rss` 6, `mit_technology_review_ai_rss` 1, `thedecoder_rss` 10;
28 stale and 28 invalid records rejected, 0 future-dated accepted.

No tab reported missing provenance or missing News full text. Jobs and News
were re-filtered at the single export cutoff, as the exporter requires.

### Artifact checks

- `submission/graphone_final.xlsx`: **PASS**. Opens with `openpyxl`; exactly
  six sheets in the required order — **Startups**, **Products**,
  **Research Papers**, **Jobs**, **News**, **Entity Mapping Log**.
  SHA-256 `2270cbbff3ed92c6a991e12ad6d17326ddc5462d162e3c347803b9ba5645b5d9`.
- `submission/graphone_final.manifest.json`: **PASS**. Its recorded SHA-256
  matches the workbook bytes; per-tab row counts match the table above.
- `submission/architecture.pdf`: **PASS**, unchanged at 12,671 bytes.
- Deterministic suite: `590 passed, 2 deselected` (live integration excluded).
- Git hygiene: working tree clean; local HEAD equals
  `origin/genspark_ai_developer`. `.local/submission.db`, `.env`, `.venv`,
  `data/raw` and run logs remain ignored and untracked. PR #4 remains **OPEN**
  and unmerged.

## Earlier checkpoint verification — 2026-09-11 UTC (historical)

**Artifact verification is blocked, not passed.** This session resumed the
existing GitHub branch `genspark_ai_developer` at
`d612ee9558fbe8ab69f8d1d36a0d8649e1ed8f2d`. The initial clean checkout was on
`phase-8-repair`; switching to the existing remote checkpoint recovered the
tracked architecture PDF, but not the local submission data. No collection,
export, PDF rebuild, freshness change or pipeline modification was performed.
The sections below this checkpoint are historical audit context, not instructions
to restart collection.

### Reported prior-session results versus current observations

| Vertical | User-reported completed count | Current DB/workbook verification |
|---|---:|---|
| Startups | 1000 | Unavailable: original DB and XLSX absent |
| Products | 1000 | Unavailable: original DB and XLSX absent |
| Research Papers | 1000 | Unavailable: original DB and XLSX absent |
| Jobs | 0 | Unavailable: original DB and XLSX absent |
| News | 45 | Unavailable: original DB and XLSX absent |

The user reports all five Jobs sources and all five News sources were exercised.
The reported Jobs/News targets were not padded: strict 24-hour freshness and
source availability/usable timestamp limitations yielded 0 Jobs and 45 News.
These are prior-session results supplied by the user, **not newly observed DB
counts**. Original source-run evidence is unavailable here; no independent
five-source execution claim is made. The Entity Mapping Log count is unknown.

### Artifact checks

- `/home/user/webapp/submission/graphone_final.xlsx`: **absent**. Opening the
  workbook, enumerating its actual sheets and counting rows cannot be verified.
- `/home/user/webapp/.local/submission.db`: **absent at session entry**; the
  `.local` directory was also absent. No empty replacement DB was created.
  Exact SQLite counts and workbook-to-DB comparison remain blocked.
- `/home/user/webapp/submission/graphone_final.manifest.json`: **absent**.
  No manifest candidate exists in `submission/`; an XLSX SHA-256 or manifest
  match cannot be claimed. Neither the XLSX nor manifest is tracked at the
  resumed checkpoint; no GitHub release was listed.
- Required six-sheet order, also confirmed by static inspection of the existing
  exporter (not by opening the absent final workbook): **Startups**, **Products**,
  **Research Papers**, **Jobs**, **News**, **Entity Mapping Log**.
- `/home/user/webapp/submission/architecture.pdf`: **PASS**. Existing tracked
  PDF is 12,671 bytes, PDF 1.4, four A4 pages, unencrypted. `pdfinfo` and strict
  `pypdf.PdfReader` parsing succeeded; all four pages yielded readable text.
  File bytes are unchanged from the resumed GitHub checkpoint. SHA-256:
  `669e7b74add2a2cfbaafe0b7e868611939449f072ddff92166395a89861b92e1`.

### Deterministic tests and Git hygiene

No full-suite pass after the latest implementation/test commit
`079e0906f9010d2c44a820bb8a54d21263b93b3f` was available in the inspected PR
record, so the final deterministic suite was run against checkpoint `d612ee9`:

```text
.venv/bin/python -m pytest -m 'not integration' --basetemp=/home/user/webapp/.venv/pytest-tmp -q
590 passed, 2 deselected, 64 warnings in 23.94s
```

The two opt-in live integration tests were excluded. Existing warnings concern
newspaper/lxml deprecations; pytest-asyncio also reports its unset fixture-loop
scope. Pinned dependencies, test temporary files and the local test log
(`.venv/final-pytest.log`) are confined to the ignored `.venv` directory.
No submission artifacts were regenerated by this verification.

Tracked-file inspection found no databases, live raw-data directories, caches
or temporary files. Recognizable private-key/GitHub/AWS/Google/provider-token
signature scans found no matches; this is not a guarantee against every possible
secret format. **Explicit existing exceptions:** `.env.example` remains tracked
with empty API/secret fields and local demo datastore credentials, and 16
`tests/fixtures/` files remain tracked as deterministic test inputs. Thus a
literal claim that no `.env*` file or captured fixture exists would be false.
No real `.env` or credential-bearing environment file was found. Existing ignore
rules protect `.env`, `.env.*` except the template, local databases, `.local`,
`data/raw` and test caches. No fixture or environment template was removed.

PR [#4](https://github.com/a1creator284/graphone-intelligence-pipeline/pull/4)
was confirmed **OPEN**, `mergedAt: null`, targeting `phase-8-repair`. There is
no `main` branch. This checkpoint changes only audit/README documentation and
must not merge the PR or rewrite the pre-existing checkpoint history.

**Remaining action:** restore the original XLSX, manifest and SQLite DB from the
previous session or its saved backup, then perform read-only verification. Do
not recollect or rebuild to conceal their absence. Existing provenance, source,
LLM/Google Sheets and scaling limitations in README remain applicable.

## Scope and source of truth

Initial audited GitHub HEAD: `3fa075423ec53160983a646e9f6ee6f2fdce4d50` on
`genspark_ai_developer`, fetched 2026-09-11 UTC. PR #4 is OPEN, targeting
`phase-8-repair`; no `main` branch exists. Do not merge or rewrite prior history.

**The assessment PDF was not supplied and is absent from the repository,
releases and issue/PR attachments inspected.** This audit covers all 20 items
in the user's milestone brief, not unidentified PDF clauses or exact column
contracts. Prior handoffs are historical claims, not current live proof.
No production database or prior 1,000-row datasets were present at checkout.

## Phase 1: initial audit (before targeted fixes)

| Requirement | Status | Repository evidence | Risk / qualification |
|---|---|---|---|
| 1. 1,000 startups | PARTIAL | `pipeline/startups.py`, `crawlers/ycombinator_startups.py`, startup/YC tests | Supported target; prior dataset unavailable; fresh run required. |
| 2. 1,000 products | PARTIAL | `pipeline/products.py`, HF/OpenRouter adapters, product tests | Supported target; count unverified here; HF pricing may be null. |
| 3. 1,000 papers | PARTIAL | `pipeline/research.py`, arXiv/OpenAlex adapters, research tests | Supported target; count unverified here; optional GitHub data absent where not published. |
| 4. Five AI news sources | PASS | Five enabled entries in `config/sources.py` and `pipeline/news.py:ADAPTER_CLASSES` | HN, TechCrunch, Verge, MIT, Decoder; enabled does not mean each supplies fresh rows. |
| 5. Five AI job sources | PASS | Five enabled entries and `pipeline/jobs.py:ADAPTER_CLASSES` | RemoteOK, WorkingNomads, HN hiring, Wellfound, BuiltIn; blocked/date-only sources may yield zero. |
| 6. Strict 24h news/jobs | PASS | Both pipelines hardcode window 24, skew 0; strict date validators; freshness/strict tests | Must filter again at export because persisted rows age. |
| 7. Gemini Flash -> Groq Llama -> DeepSeek | PARTIAL | `llm/orchestrator.py`, three providers, default provider order, provider/orchestrator tests | Keys/models unset; no live LLM call verified; deterministic ingestion does not invoke LLM. |
| 8. 413 chunking | PASS | `_generate_chunk`, `llm_max_split_depth`, chunker/orchestrator tests | Bounded recursive split; irreducible payload fails explicitly. |
| 9. 429 exponential backoff + jitter | PASS | `crawlers/retry.py`, HTTP/provider tests | Bounded Retry-After; excessive server cooldown aborts instead of retrying early. |
| 10. Entity resolution | PASS | `resolution/`, jobs/startups/products integration, mapping log model/tests | High-threshold fuzzy matches still need human review; in-memory candidate state. |
| 11. Provenance | PARTIAL | `RawDocument` foreign keys/hash/URL/time; Jobs stores raw body; News stores extracted text | Startups/products/research store hash metadata but do not archive raw response bytes. |
| 12. Anti-bot / JS strategy | PASS | Source registry plus `crawlers/http.py` | API/feed/structured public alternatives; blocked sources skipped, no implemented browser fallback or automated robots enforcement. |
| 13. Targets/pagination/dedup | PARTIAL | YC batch facets, HF cursors, arXiv/OpenAlex pages, DB constraints and tests | News target is discovery allocation, not global row ceiling; sources and validation can cause honest shortfalls. |
| 14. Bounded concurrency/backpressure | PASS | `pipeline/workers.py` rolling window; 21 worker tests | Bounds outstanding tasks, not total records/raw bytes/resolver state. |
| 15. Theoretical 500k+ scalability | PASS | README scalability audit and unchanged business-rule interfaces | Architectural path only; distributed dispatch/batches/checkpoints/shared raw store require implementation; no 500k proof. |
| 16. Exactly six output tabs | FAIL | `src/export/__init__.py` empty; CLI logs `export_not_yet_wired` and returns success | Real blocker: implement XLSX through existing ORM models. |
| 17. README.md | PARTIAL | Tracked README contains setup, sources, testing, scalability | Historical status/stub claims are stale; final instructions needed. |
| 18. architecture.pdf | FAIL | No tracked PDF | Real blocker: describe actual system and explicit scale limits. |
| 19. No fabricated/padded data | PASS | Source-based adapters, null missing fields, validation/rejections, no target padding | Tests and code evidence; live export requires actual DB inspection. |
| 20. Repository safety | PARTIAL | Clean initial tracked tree; `.env`/venv/raw paths ignored | SQLite/local run artifacts not yet ignored; protect before creating demo DB. |

## Targeted work authorized by this audit

1. Protect local SQLite/run artifacts; do not change ingestion/resolution/LLM code.
2. Implement the missing six-tab XLSX exporter and CLI wiring with export-time
   freshness, literal spreadsheet cells, provenance joins and mapping history.
3. Run existing vertical CLI commands into a new local, ignored SQLite DB;
   export only actual persisted records, reporting all source shortfalls.
4. Create architecture.pdf and update final submission instructions/evidence.
5. Run focused/full tests and safety checks; preserve each milestone on GitHub.

No Google Sheets credentials or LLM provider credentials/models are available
in this session. No Google Sheet or live LLM success will be claimed.
