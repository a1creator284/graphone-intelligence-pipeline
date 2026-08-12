# Phase 8: LLM Orchestration Requirements

## 1. Provider Strategy & Model Configuration
1. The engine MUST orchestrate LLM calls natively within the intelligence pipeline, supporting Gemini, Groq, and DeepSeek.
2. **Fallback Order:** Configured via `llm_provider_order` (default: `["gemini", "groq", "deepseek"]`).
3. **Model Configuration:** Provider model identifiers must be supplied through configuration. `GEMINI_MODEL`, `GROQ_MODEL`, and `DEEPSEEK_MODEL` must be added to `Settings` (environment variables). The engine MUST NOT hardcode speculative/deprecated model identifiers.
4. API keys are sourced from the environment (`GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`).

## 2. Token Estimation & Chunking Strategy
1. **Token Estimation:** The engine MUST use a deterministic heuristic: `estimated_tokens = math.ceil(len(text) / 4)` where `len(text)` is the character length of the UTF-8 string. Empty string yields 0. No network calls or provider-specific tokenizers are permitted. This is strictly an estimate.
2. **Chunking Trigger:** If `estimated_tokens <= llm_token_budget` (default 12000), exactly one chunk containing the original payload is returned. If it exceeds the budget, it is chunked.
3. **Chunking Mechanism:** Split deterministically at newline boundaries where possible. If a single line exceeds the budget, split at character boundaries.
4. **Chunking Properties:** Original ordering MUST be preserved. Semantic/LLM-generated boundaries MUST NOT be used. Text MUST NOT be silently discarded or duplicated. Empty payloads return zero chunks. Concatenating chunks in order reproduces the original payload exactly.
5. **Dynamic Trigger:** If a `PayloadTooLargeError` (HTTP 413) is encountered, it MUST immediately propagate (no retries for the identical payload) and invoke the chunker.

## 3. Generic Prompt & Multi-Chunk Aggregation Contract
1. **System Prompt Contract:** The LLM engine must expose a generic interface separating:
   - System instructions (defines behavior)
   - User payload (raw text/chunk)
   - Requested Pydantic schema (structured output)
   Provider adapters must not invent prompt formats; the orchestrator owns this generic contract.
2. **Aggregation Contract:** The generic engine MUST NOT invent field-by-field dictionary merging for arbitrary Pydantic models. 
   - A single-chunk request returns that chunk's single validated Pydantic object.
   - A multi-chunk request returns a list of validated Pydantic objects.

## 4. Rate Limiting, Validation, & Error Strategy
The engine enforces a strict Retry and Fallback Matrix using the unified `retry_async` utility:

| Condition | Retry (Same Provider) | Fallback | Chunk | Error Class |
| :--- | :--- | :--- | :--- | :--- |
| 429 Too Many Requests | Yes | After exhaustion | No | `RateLimitError` |
| Rate-limit 403 | Yes | After exhaustion | No | `RateLimitError` |
| 5xx Server Error | Yes | After exhaustion | No | `NetworkError` / `PipelineError` |
| Timeout | Yes | After exhaustion | No | `TimeoutErrorPipeline` |
| 401 Unauthorized | No | Immediate | No | `AuthenticationError` |
| Non-rate-limit 403 | No | Immediate | No | `AuthenticationError` |
| 413 Payload Too Large | No | No | Yes | `PayloadTooLargeError` |
| Malformed JSON | Yes | After exhaustion | No | `ParsingError` |
| Pydantic ValidationError | Yes | After exhaustion | No | `ValidationError` |
| All providers exhausted | Exhausted | Terminal Error | No | `ProviderUnavailableError` |

*Note on Validation:* Structurally invalid provider output is NEVER accepted. Malformed JSON and schema mismatches trigger a retry attempt against the same provider up to `llm_max_retries`. If exhausted, the engine falls back to the next provider.

## 5. Observability
1. **EVERY PROVIDER ATTEMPT** (successful, retry, failed, fallback) MUST create a persistent `LLMRequest` record in the database.
2. The record MUST contain the fields explicitly supported by the existing model (`provider`, `model`, `status`, `error_type`, `latency_ms`, `input_size_chars`, `estimated_tokens`, `output_size_chars`, `retry_count`, `fallback_used`, etc.). No speculative cost-tracking fields will be added.

## 6. Testing
1. LLM clients MUST NOT make live network calls in the default test suite.
2. The orchestrator and fallback logic MUST be fully tested using mocked HTTP transports (`respx` or `httpx.MockTransport`).
