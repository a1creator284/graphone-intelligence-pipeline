# Claude Handoff

Operational checkpoint for resuming work on the GraphOne / FrontierAtlas
intelligence pipeline. Keep this file short and factual.

**Last updated:** 2026-09-09
**Branch:** `phase-8-repair`
**Repo:** https://github.com/a1creator284/graphone-intelligence-pipeline

## Current Status

Phases 1-8 are implemented and green: **249 passed, 2 deselected** (the 2
deselected are `-m integration`, live-network, opt-in).

This session was a **verification + repair** pass, not a new feature phase.
It found and fixed a real regression (README wiped) and corrected two
documentation claims that live testing proved false.

## Completed

- Rebuilt the dev environment from scratch (`.venv`, Python 3.13, all pins
  from `requirements.txt` install cleanly despite `requires-python >=3.11`).
- Ran the full suite: **249 passed** in ~7s.
- **Fixed regression:** `README.md` had been truncated from 13,953 bytes to
  1 byte by commit `d87a4a1` ("feat: complete Phase 8 LLM Orchestration
  engine"), which deleted 257 lines. Restored from `53f9520` and brought
  current through Phase 8.
- **Fixed:** corrupted trailing sentence in
  `.kiro/specs/phase-8-llm-orchestration/tasks.md` (`...Phase 8
  complete.iant with the original master prompt.`).
- Documented the Phase 8 LLM orchestration engine in the README (providers,
  fallback, chunker, 413 path, `LLMRequest` observability).
- Live-verified the research and news pipelines end-to-end (see Tests).
- Flagged historical sections of `docs/DEVELOPMENT_HANDOFF.md` as superseded.

## In Progress

Nothing half-done. Working tree is clean and committed.

## Files Changed

- `README.md` — restored from truncation + Phase 8 section + corrected
  environment/live-verification section
- `docs/DEVELOPMENT_HANDOFF.md` — phase table extended to 8; added a banner
  marking the Phase 4-5 environment claims as historical/superseded
- `.kiro/specs/phase-8-llm-orchestration/tasks.md` — fixed corrupted text
- `CLAUDE_HANDOFF.md` — new (this file)

No `src/` or `tests/` changes — application code needed no repair.

## Tests

```
$ .venv/bin/python -m pytest -q
249 passed, 2 deselected, 31 warnings in 7.20s
```

Live CLI runs (real network, not mocked):

```
$ DATABASE_URL="sqlite+aiosqlite:///:memory:" python -m src.main --vertical research --target 2
discovered: 2, valid_records: 1, by_source: {arxiv: 1, papers_with_code: 0}

$ DATABASE_URL="sqlite+aiosqlite:///:memory:" python -m src.main --vertical news --target 5
discovered: 4, full_text_extracted: 3, validated: 3, rejected_stale: 1, persisted: 3
```

## Known Issues

1. **Papers With Code is dead upstream (highest priority).**
   `paperswithcode.com/api/v1/papers/` 302-redirects to
   `huggingface.co/papers/trending` and returns HTML, not JSON. The adapter
   fails *correctly* (raises `ParsingError`, logs `parse_failed`, yields 0
   records, run continues) but the source is permanently gone. Replace with
   HuggingFace Papers or Semantic Scholar behind the same adapter interface.
   The research vertical is effectively single-source (arXiv) until then.

2. **Phase 8 has never made a live LLM call.** No `GEMINI_API_KEY` /
   `GROQ_API_KEY` / `DEEPSEEK_API_KEY` is configured here. All 18 Phase 8
   tests are `respx`-mocked against each vendor's documented response shape.
   Do a real call before trusting it in production.

3. **Phase 8 is not wired into any CLI vertical.** The orchestrator is built
   and tested but has no caller yet; Phases 9-11 are its first consumers.

4. `hackernews_ai` returned 0 records in the live run — not a bug, just
   nothing inside the 24h freshness window at that moment. Re-check.

5. No Alembic migrations; schema comes from `Base.metadata.create_all`.

6. 31 test warnings (deprecations from `newspaper3k` / `bs4` / pytest-asyncio
   fixture loop scope). Cosmetic, but worth clearing.

7. `pyproject.toml` says `requires-python = ">=3.11"`; only 3.13 is present
   here and everything installs/passes on it.

## Remaining Assessment Requirements

Phases 9-16 from the master prompt:

- Phase 9-11: startups + products pipelines (first real consumers of the
  Phase 8 LLM orchestrator)
- Phase 12: entity resolution (normalization, aliases, fuzzy matching via
  `rapidfuzz`, mapping log) — `src/resolution/` is still an empty stub
- Phase 13: quality / metrics layer
- Phase 14: CSV / XLSX / Google Sheets export — `src/export/` is an empty stub
- Phase 15: architecture documentation (architecture.pdf)
- Phase 16: final requirement-matrix audit

## Next Task

**Replace the dead Papers With Code adapter.** Concretely:

1. Add `src/crawlers/huggingface_papers.py` (or `semantic_scholar.py`)
   implementing the same adapter interface as
   `src/crawlers/papers_with_code.py`.
2. Register it in `src/config/sources.py` with the correct access tier.
3. Add a fixture in `tests/fixtures/` matching the real API response shape
   and a test file mirroring `tests/test_papers_with_code_adapter.py`.
4. Swap it into `src/pipeline/research.py` (note: `per_adapter_target` is a
   naive `target // 2` split — revisit if one source runs dry).
5. Decide whether to delete the PWC adapter or keep it as a documented
   dead-source example.

Then move to Phase 9 (startups pipeline).

## Important Decisions

- **Restored the README rather than rewriting it.** `git show 53f9520:README.md`
  recovered the pre-truncation content; only additive Phase 8 edits and
  factual corrections were layered on top.
- **Corrected the "restricted sandbox" narrative.** Earlier docs claimed
  arXiv and RSS feeds were unreachable. That is no longer true and live runs
  prove it, so the README now leads with verified live results. The old text
  is marked historical, not deleted, to preserve the audit trail.
- **Did not "fix" the Papers With Code adapter.** Its failure mode is already
  the correct one (fail loudly, contribute zero, don't fabricate). The
  problem is the upstream source, so the fix is replacement, not patching.
- **Left `src/` untouched.** Everything passed; changing working code during
  a verification pass would add risk with no benefit.

## Environment / Dependencies

- Python 3.13 (`/usr/bin/python3.13`); project targets >=3.11
- Virtualenv at `.venv/` (gitignored) — must be recreated on a fresh sandbox
- All deps from `requirements.txt`; `playwright` installs but no browser
  binaries are downloaded (not needed by current tests)
- No Postgres/Redis/Docker daemon locally — use
  `DATABASE_URL="sqlite+aiosqlite:///:memory:"` for local runs
- Network egress is **open** in this environment
- No LLM API keys configured

## Blockers

None blocking. Papers With Code being dead is a scope change, not a blocker —
arXiv alone keeps the research vertical functional.

## Continue With

```bash
cd /home/user/webapp

# Recreate the environment (the venv is gitignored)
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Confirm the baseline is still green (expect: 249 passed, 2 deselected)
.venv/bin/python -m pytest -q

# Live smoke tests
DATABASE_URL="sqlite+aiosqlite:///:memory:" .venv/bin/python -m src.main --vertical research --target 2
DATABASE_URL="sqlite+aiosqlite:///:memory:" .venv/bin/python -m src.main --vertical news --target 5

# Confirm Papers With Code is still dead before replacing it
curl -sL -o /dev/null -w "%{http_code} -> %{url_effective}\n" https://paperswithcode.com/api/v1/papers/

# Then start the next task
git checkout -b phase-9-startups
```
