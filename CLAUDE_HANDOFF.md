# Claude Handoff

Operational checkpoint for resuming work on the GraphOne / FrontierAtlas
intelligence pipeline. Keep this file short and factual.

**Last updated:** 2026-09-09 (session 2)
**Branch:** `phase-8-repair`
**Repo:** https://github.com/a1creator284/graphone-intelligence-pipeline

## Current Status

Phases 1-8 **plus Phase 12 (entity resolution)** are implemented and green:
**286 passed, 2 deselected** (the 2 deselected are `-m integration`,
live-network, opt-in). Baseline at session start was 249; this session added
37 tests and broke nothing.

This session did a targeted gap audit against the assessment requirements
and then implemented the single highest-value missing capability.

## What This Session Did

### Gap audit (findings)

| # | Question | Finding |
|---|---|---|
| A | How does research reach 1,000 papers? | arXiv paging only; `target // 2` split across 2 adapters, one of which is dead |
| B | Usable research sources | **1** (arXiv). PWC re-confirmed dead: 302 → `huggingface.co/papers/trending`, HTML |
| C | PWC replacement | **OpenAlex verified HTTP 200, no key.** Semantic Scholar returns **429** unauthenticated |
| D | Startups → 1,000 | **Not implemented.** YC Algolia endpoint now returns **403** |
| E | Products → 1,000 | **Not implemented.** Product Hunt GraphQL needs an OAuth token |
| F | News sources | 5 configured, 4 fetched live; volume gated by the 24h window |
| G | Job sources | 5 configured, 4 ran live (WorkingNomads fetch-failed this run) |
| H | Entity resolution connected? | **Was: nowhere.** `src/resolution/` was an empty stub → **fixed this session** |
| I | Provenance per vertical | Yes for research / news / jobs (`RawDocument` + `raw_document_id` FK) |
| J | LLM orchestrator callable by pipelines? | Built + tested, still **no production caller** |
| K | Gemini → Groq → DeepSeek order | Enforced via `settings.llm_provider_order` default |
| L | 413 / 429 | Implemented (`retry.py` backoff+jitter, `chunker.py` 413 split) and tested |
| M | Six export tabs | **No export module at all** (`src/export/` still empty) |
| N | CLI per vertical | research / news / jobs run end-to-end; startups / products do not |

### Milestone implemented: Phase 12 — Entity Resolution

Chosen over the PWC replacement because it was a **completely unimplemented
assessment requirement** (req #10) that also unblocks a required export tab
(#13, "Entity Mapping Log"), and it is fully verifiable offline. arXiv can
page to 1,000 papers on its own, so the dead PWC adapter is a
volume/redundancy problem, not a hard blocker.

- `src/resolution/normalize.py` — pure deterministic normalization: NFKD
  accent folding, casefold, `&`→`and`, punctuation→space, then *trailing*
  legal-suffix stripping from a fixed list (`inc`, `llc`, `ltd`, `gmbh`, …),
  leading-article strip, trailing-noise strip. `"Incredible AI"` keeps its
  `Inc` because only trailing tokens are eligible.
- `src/resolution/resolver.py` — `EntityResolver` with a 5-rung ladder:
  `normalized_exact` (1.00) → `alias` (0.99) → `fuzzy` (score/100, rapidfuzz
  `token_sort_ratio` ≥ 92) → `created` (1.00) → `unresolved` (0.00). Warms an
  in-memory index from `canonical_entities` + `entity_aliases`, registers a
  new alias row on every fuzzy hit, and writes **every** decision to
  `entity_mapping_log`.
- Wired into `src/pipeline/jobs.py`: every persisted `Job` now carries a real
  `canonical_entity_id`; resolver exceptions degrade to a null link instead
  of dropping the record. New counters on `JobsPipelineResult`:
  `entities_resolved`, `entities_created`, `entities_unresolved`,
  `entity_methods`.

**Anti-fabrication properties (deliberate):**
- A false merge is worse than a false split. Threshold is high (92);
  near-misses in the 85-92 band are **not** merged — they become separate
  entities and the near-miss is logged as a review candidate.
- Unusable names (empty / punctuation-only / single char) resolve to
  `unresolved` with a null FK and confidence 0.0. Still audited, never
  replaced with a placeholder.
- Freshness rejection happens *before* resolution, so stale records create
  zero phantom entities (explicitly tested).

## Files Changed

**New**
- `src/resolution/normalize.py`
- `src/resolution/resolver.py`
- `tests/test_entity_normalization.py` (17 tests)
- `tests/test_entity_resolver.py` (16 tests)
- `tests/test_jobs_entity_resolution.py` (4 tests)

**Modified**
- `src/resolution/__init__.py` — was empty; now the public API surface
- `src/pipeline/jobs.py` — resolver wired in + 4 new result counters
- `README.md` — new "Entity resolution / deduplication (Phase 12)" section,
  phase table updated, "Next phases" corrected with the live source probes
- `CLAUDE_HANDOFF.md` — this file

No other `src/` file touched. No existing test modified or deleted.

## Tests

```
$ .venv/bin/python -m pytest -q
286 passed, 2 deselected, 31 warnings in 8.60s
```

Baseline before the change was `249 passed, 2 deselected` — verified by
running the suite on a clean checkout before writing any code.

New tests in isolation:

```
$ .venv/bin/python -m pytest tests/test_entity_normalization.py tests/test_entity_resolver.py -q
33 passed in 1.04s

$ .venv/bin/python -m pytest tests/test_jobs_entity_resolution.py -q
4 passed in 0.81s
```

Live run proving resolution executes against real network data:

```
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" python -m src.main --vertical jobs --target 20
persisted: 1, rejected_stale: 206,
entities_resolved: 1, entities_created: 1, entities_unresolved: 0,
entity_methods: {"created": 1}
```

(206 stale rejections are the HN "Who is hiring?" comments falling outside
the 24h window — correct behaviour, not a bug.)

## Known Issues

1. **Papers With Code is still dead.** Re-confirmed this session. Research is
   effectively single-source (arXiv). **OpenAlex is verified reachable
   (HTTP 200, no key)** — that is the recommended replacement. Semantic
   Scholar returned **429** unauthenticated, so it is a worse choice.
2. **Startups and products verticals do not exist.** Requirements #1 and #2
   (1,000 each) are unmet. Source access has degraded since the registry was
   written: YC Algolia → **403**, `ycombinator.com/companies/directory.json`
   → **404**, Product Hunt GraphQL → needs OAuth. **Re-verify source access
   before writing adapters.**
3. **No export module.** `src/export/` is still empty; requirement #13 (six
   tabs) is unmet. All six backing tables now exist and are populated for
   the three live verticals.
4. **Phase 8 has never made a live LLM call.** No API keys configured. All
   Phase 8 tests are `respx`-mocked. Live verification remains pending.
5. **Phase 8 still has no production caller.** The orchestrator is not
   invoked by any pipeline.
6. Entity resolution is wired into **jobs only**. `Startup` and `Product`
   have the same FK column and will use the identical resolver; `News` and
   `ResearchPaper` have no company field, so they legitimately don't need it.
7. No `architecture.pdf` (requirement #15).
8. No Alembic migrations; schema comes from `Base.metadata.create_all`.
9. 31 test warnings (deprecations from `newspaper3k` / `bs4` /
   pytest-asyncio fixture loop scope). Cosmetic.

## Remaining Assessment Gaps

| Req | Item | Status |
|---|---|---|
| 1 | 1,000 startups | ❌ no pipeline |
| 2 | 1,000 products | ❌ no pipeline |
| 3 | 1,000 papers | ⚠️ arXiv only (single source) |
| 4 | ≥5 news sources | ✅ 5 configured |
| 5 | ≥5 job sources | ✅ 5 configured |
| 6 | 24h freshness | ✅ enforced + live-proven |
| 7 | Gemini→Groq→DeepSeek | ✅ built, ❌ never called live |
| 8 | 413 chunking | ✅ |
| 9 | 429 backoff+jitter | ✅ |
| 10 | Entity resolution | ✅ **this session** |
| 11 | Provenance | ✅ for the 3 live verticals |
| 12 | Anti-bot strategy | ✅ documented tiering |
| 13 | Six-tab export | ❌ not built |
| 14 | README | ✅ |
| 15 | architecture.pdf | ❌ not built |
| 16 | 500k+ scale story | ✅ documented |

## Next Task

**Replace the dead Papers With Code adapter with OpenAlex.** It is now the
highest-priority *unblocked* gap: requirement #3 is degraded to a single
source, and OpenAlex is the only verified-reachable, key-free replacement.

1. `curl "https://api.openalex.org/works?filter=concepts.id:C154945302&per-page=2"`
   to re-confirm reachability and capture the real response shape.
2. Add `src/crawlers/openalex.py` implementing the same adapter interface as
   `src/crawlers/arxiv.py` (mirror `papers_with_code.py`'s structure).
   OpenAlex asks for a `mailto=` param for the polite pool — include it.
3. Register it in `src/config/sources.py` (`Vertical.RESEARCH`,
   `OFFICIAL_API`) and mark `papers_with_code` `enabled=False` with a note
   rather than deleting it — the dead-source handling is good evidence.
4. Save a real captured response to `tests/fixtures/` and add
   `tests/test_openalex_adapter.py` mirroring
   `tests/test_papers_with_code_adapter.py`.
5. Swap it into `ADAPTER_CLASSES` in `src/pipeline/research.py`.
   ⚠️ `per_adapter_target` is a naive `target // len(ADAPTER_CLASSES)` split —
   if one source runs dry the target is silently missed. Consider making the
   split fill-forward.
6. **Do not fabricate any paper metadata.** Fields OpenAlex doesn't supply
   (e.g. a GitHub repo link) stay `NULL`.

After that, the biggest remaining wins are the six-tab export (#13) and
`architecture.pdf` (#15) — both are self-contained and low-risk.

## Important Decisions

- **Chose entity resolution over the PWC replacement**, contradicting the
  previous handoff's "next task". Rationale: entity resolution was a
  requirement with *zero* implementation and it unblocks a required export
  tab, whereas arXiv alone can still page to 1,000 papers — so PWC is a
  redundancy gap, not a blocker. The task brief explicitly permitted
  overriding the old handoff on priority grounds.
- **Set the fuzzy threshold at 92, not lower.** Aggressive merging inflates
  apparent dedup quality while silently corrupting the dataset. Splitting is
  visible in the mapping log and reversible.
- **Logged unresolved names instead of skipping them.** An empty audit row
  is more honest than a missing one and keeps the mapping-log tab complete.
- **Made resolution non-fatal.** A resolver exception nulls the FK and lets
  the job persist. Losing verified job data over a name-matching failure
  would be the wrong trade.
- **Left the research/news pipelines untouched.** Neither has a company
  field, so wiring the resolver there would add risk with no benefit.

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

None blocking. Startups/products source access (YC 403, PH OAuth) is the
only genuinely uncertain area and needs re-probing before Phases 9-11.

## Continue With

```bash
cd /home/user/webapp

# Recreate the environment (the venv is gitignored)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Confirm the baseline is still green (expect: 286 passed, 2 deselected)
.venv/bin/python -m pytest -q

# Re-probe sources before building against them
curl -s -o /dev/null -w "openalex: %{http_code}\n" \
  "https://api.openalex.org/works?filter=concepts.id:C154945302&per-page=2"
curl -sL -o /dev/null -w "pwc: %{http_code} -> %{url_effective}\n" \
  https://paperswithcode.com/api/v1/papers/

# Live smoke tests
DATABASE_URL="sqlite+aiosqlite:///:memory:" .venv/bin/python -m src.main --vertical research --target 2
DATABASE_URL="sqlite+aiosqlite:///:memory:" .venv/bin/python -m src.main --vertical jobs --target 20

# Then start the next task: src/crawlers/openalex.py
```
