# Phase 8: LLM Orchestration Tasks

- [x] **Task 1: Provider Abstraction & Configuration**
  - *Objective*: Define generic `LLMProvider` interface and add `GEMINI_MODEL`, `GROQ_MODEL`, `DEEPSEEK_MODEL` to `Settings`.
  - *Files*: `src/config/settings.py`, `src/llm/providers/base.py`
  - *Dependency*: None.
  - *Tests*: `tests/test_settings.py` checking validation of models.
  - *Acceptance Criteria*: Settings enforce models as configurable strings.

- [x] **Task 2: Provider Request Implementations**
  - *Objective*: Implement Gemini, Groq, and DeepSeek providers wrapping the internal `AsyncHttpClient`.
  - *Files*: `src/llm/providers/gemini.py`, `src/llm/providers/groq.py`, `src/llm/providers/deepseek.py`
  - *Dependency*: Task 1.
  - *Tests*: `tests/test_llm_providers.py` (mocked `respx` ensuring proper Pydantic JSON parsing and HTTP error mapping).
  - *Acceptance Criteria*: Providers raise native `ParsingError` or `ValidationError` on malformed output, and translate 429s to `RateLimitError`.

- [x] **Task 3: LLM Orchestrator + Retry/Fallback**
  - *Objective*: Create the central engine to iterate through `llm_provider_order` handling retries natively with `retry_async`.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 2.
  - *Tests*: `tests/test_llm_orchestrator.py` (Fallback chains, exhaustion, exceptions).
  - *Acceptance Criteria*: Correct fallback iteration and terminal `ProviderUnavailableError`.

- [x] **Task 4: LLMRequest Observability**
  - *Objective*: Persist provider metrics tracking exactly `model`, `retry_count`, `latency_ms`, and `fallback_used` per execution logic.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 3.
  - *Tests*: Verify the DB row counts/properties are accurate on failures and successes.
  - *Acceptance Criteria*: DB records match orchestration behavior cleanly.

- [x] **Task 5: Deterministic Chunker**
  - *Objective*: Implement the token chunker that enforces `llm_token_budget` mathematically.
  - *Files*: `src/extraction/chunker.py`
  - *Dependency*: Task 4.
  - *Tests*: Exact token length estimation tests and chunking boundary integrity tests.
  - *Acceptance Criteria*: Guaranteed safe concatenation and zero text dropped.

- [x] **Task 6: 413 / Chunk Integration**
  - *Objective*: Connect the `PayloadTooLargeError` path natively into the orchestrator logic to chunk on the fly if needed.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 5.
  - *Tests*: 413 mocked trigger ensuring proper extraction loop instead of a retry loop.
  - *Acceptance Criteria*: A 413 triggers chunking logic without falling back into a retry infinite loop.

- [x] **Task 7: Final Verification + Documentation**
  - *Objective*: Audit project, clean up any scratch files, and integrate final documentation.
  - *Files*: `README.md`, `.kiro/specs/phase-8-llm-orchestration/tasks.md`
  - *Dependency*: Task 6.
  - *Tests*: `pytest -v` across all tasks.
  - *Acceptance Criteria*: Zero remaining `[ ]` tasks, clean git state, Phase 8 complete and compliant with the original master prompt.
