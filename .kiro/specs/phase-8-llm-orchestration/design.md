# Phase 8: LLM Orchestration Design

## 1. Component Architecture
1. **Orchestrator (`src/llm/orchestrator.py`)**: Central API exposing `generate(system_instruction: str, user_payload: str, schema: type[BaseModel]) -> BaseModel | list[BaseModel]`. Handles the fallback loop over providers and orchestrates aggregation.
2. **Provider Adapters (`src/llm/providers/`)**: 
   - Abstract base class `LLMProvider` defining the specific generation format enforcing JSON output natively.
   - `GeminiProvider`, `GroqProvider`, `DeepSeekProvider` wrapping the internal `AsyncHttpClient`.
3. **Chunker (`src/extraction/chunker.py`)**: Uses the deterministic `math.ceil(len(text)/4)` heuristic to split text payloads strictly by newlines/characters if they exceed `llm_token_budget`.

## 2. Provider Abstraction & Configuration
- Providers must conform to the abstraction returning structured Pydantic models.
- Environment variables configured in `Settings`: `GEMINI_MODEL`, `GROQ_MODEL`, `DEEPSEEK_MODEL`.
- `llm_provider_order` determines the fallback chain array dynamically at runtime.

## 3. Fallback Mechanism & Retry Interaction
- The `orchestrator.py` iterates over the `llm_provider_order` list sequentially.
- For each provider, execution is wrapped in `retry_async(..., max_attempts=llm_max_retries)`.
- If `ValidationError` or `ParsingError` (Malformed JSON) is raised by the provider adapter, it counts as a failed attempt and is caught by `retry_async`, triggering backoff on the SAME provider.
- If a provider burns through all its retries, or throws an `AuthenticationError` (401/403 non-rate-limit), the orchestrator catches the exhausted exception, logs it, and moves to the next provider.
- Total exhaustion raises a terminal `ProviderUnavailableError`.

## 4. Chunking Architecture
- **Token Estimation**: Computed deterministically as `math.ceil(len(text) / 4)`. No network calls.
- **Trigger**: Occurs preemptively based on the token budget, AND dynamically triggered if `PayloadTooLargeError` (413) is raised by the HTTP client.
- **Aggregation**: Multi-chunk extraction returns a `list[BaseModel]`. The generic engine strictly avoids dictionary merging to maintain schema safety.

## 5. Rate Limiter
- Uses the unified `AsyncHttpClient`.
- When an API responds with HTTP 429 or HTTP 403 + rate limit headers, the provider adapter raises a `RateLimitError` containing `retry_after_seconds`.
- `retry_async` processes the delay natively using full jitter logic on the same provider.

## 6. Validation Flow
- Responses are forced into JSON mode natively by the provider APIs.
- Parsed dicts are cast into the target Pydantic `schema` parameter.
- `ValidationError` triggers a native `retry_async` retry loop. Structurally invalid or schema-invalid data is never accepted.

## 7. Error Model & Observability
- `LLMRequest` database instances are committed for EVERY ATTEMPT in the fallback chain to track stability and metrics.
- Populated fields: `provider`, `model`, `status`, `error_type`, `latency_ms`, `input_size_chars`, `estimated_tokens`, `output_size_chars`, `retry_count`, `fallback_used`. 
- No speculative billing/token cost schemas are modeled.

## 8. Testing Architecture
- Uses `httpx.MockTransport` / `respx` inside Pytest to simulate provider specific 200 JSON, 429 RateLimit, 413 Too Large, 401 Auth, and 500 Server Errors, asserting fallback and chunking mechanics deterministically.
