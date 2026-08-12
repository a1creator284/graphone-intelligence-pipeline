# Phase 8: LLM Orchestration Tasks

- [x] **Task 1: Provider Abstraction & Configuration**
  - *Objective*: Define generic `LLMProvider` interface and add `GEMINI_MODEL`, `GROQ_MODEL`, `DEEPSEEK_MODEL` to `Settings`.
  - *Files*: `src/config/settings.py`, `src/llm/providers/base.py`
  - *Dependency*: None.
  - *Tests*: `tests/test_settings.py` checking validation of models.
  - *Acceptance Criteria*: Settings enforce models as configurable strings.

- [ ] **Task 2: Provider Request Implementations**
  - *Objective*: Implement Gemini, Groq, and DeepSeek providers wrapping the internal `AsyncHttpClient`.
  - *Files*: `src/llm/providers/gemini.py`, `src/llm/providers/groq.py`, `src/llm/providers/deepseek.py`
  - *Dependency*: Task 1.
  - *Tests*: `tests/test_llm_providers.py` (mocked `respx` ensuring proper Pydantic JSON parsing and HTTP error mapping).
  - *Acceptance Criteria*: Providers raise native `ParsingError` or `ValidationError` on malformed output, and translate 429s to `RateLimitError`.

- [ ] **Task 3: Retry/Fallback Orchestration**
  - *Objective*: Implement the central orchestrator iterating over `llm_provider_order` and utilizing `retry_async`.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 2.
  - *Tests*: `tests/test_llm_orchestrator.py` demonstrating 500 error fallbacks, 429 backoff on the same provider, and 401 instant fallbacks.
  - *Acceptance Criteria*: Fallback matrix strictly obeyed. Structurally invalid data triggers retry, not immediate fallback.

- [ ] **Task 4: LLMRequest Observability**
  - *Objective*: Ensure every attempt (including retries and fallbacks) logs and commits an `LLMRequest` model to the database.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 3.
  - *Tests*: `tests/test_llm_observability.py` validating correct row count insertion after simulated 500 fallback events.
  - *Acceptance Criteria*: Database holds precise records for all attempts, mapping exactly to the existing schema.

- [ ] **Task 5: Deterministic Chunker**
  - *Objective*: Implement text-splitting by newlines/characters using `math.ceil(len(text)/4)` heuristic logic.
  - *Files*: `src/extraction/chunker.py`
  - *Dependency*: Task 4.
  - *Tests*: `tests/test_chunker.py` verifying exact boundary splits and order preservation without LLM intervention.
  - *Acceptance Criteria*: Deterministic splits exactly equal to original text when joined.

- [ ] **Task 6: 413/Chunk Integration**
  - *Objective*: Integrate the chunker into the orchestrator. Implement pre-emptive splitting based on `llm_token_budget` and dynamic splitting upon `PayloadTooLargeError`.
  - *Files*: `src/llm/orchestrator.py`
  - *Dependency*: Task 5.
  - *Tests*: `tests/test_llm_chunking.py` mocking a 413 response that forces the orchestrator to split and aggregate a list of Pydantic models.
  - *Acceptance Criteria*: 413 triggers instant split. Multi-chunk returns `list[BaseModel]`.

- [ ] **Task 7: Final Verification & Documentation**
  - *Objective*: Complete end-to-end sandbox verification and update project documentation.
  - *Files*: `README.md`
  - *Dependency*: Task 6.
  - *Tests*: Full test suite execution ensuring 0 regressions.
  - *Acceptance Criteria*: Phase 8 is formally sealed and compliant with the original master prompt.
