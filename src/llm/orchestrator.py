"""
Phase 8 LLM orchestration.

Handles provider fallback/retry, deterministic chunking, dynamic 413
re-chunking, Pydantic validation, and one LLMRequest row per provider attempt.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from src.config.settings import get_settings
from src.crawlers.retry import retry_async
from src.errors import (
    NetworkError,
    ParsingError,
    PayloadTooLargeError,
    PipelineError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutErrorPipeline,
    ValidationError,
)
from src.extraction.chunker import chunk_text, estimate_tokens
from src.llm.providers.base import LLMProvider

T = TypeVar("T", bound=BaseModel)


class LLMOrchestrator:
    """Central Phase 8 LLM engine."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        *,
        session_scope_fn: Callable | None = None,
    ) -> None:
        self.settings = get_settings()
        self.providers = providers
        self._session_scope_fn = session_scope_fn

    async def generate(
        self,
        system_instruction: str,
        user_payload: str,
        schema: type[T],
        *,
        request_id: str | None = None,
        source_url: str | None = None,
        record_type: str | None = None,
    ) -> T | list[T]:
        if not user_payload:
            return []

        budget = max(1, self.settings.llm_token_budget * 4)
        chunks = chunk_text(user_payload, char_budget=budget)
        results: list[T] = []

        for chunk in chunks:
            results.extend(
                await self._generate_chunk(
                    system_instruction=system_instruction,
                    payload=chunk,
                    schema=schema,
                    request_id=request_id,
                    source_url=source_url,
                    record_type=record_type,
                )
            )

        return results[0] if len(results) == 1 else results

    async def _generate_chunk(
        self,
        *,
        system_instruction: str,
        payload: str,
        schema: type[T],
        request_id: str | None,
        source_url: str | None,
        record_type: str | None,
    ) -> list[T]:
        try:
            return [
                await self._generate_with_fallback(
                    system_instruction=system_instruction,
                    payload=payload,
                    schema=schema,
                    request_id=request_id,
                    source_url=source_url,
                    record_type=record_type,
                )
            ]
        except PayloadTooLargeError:
            if len(payload) <= 1:
                raise

            smaller = chunk_text(payload, char_budget=max(1, len(payload) // 2))
            results: list[T] = []
            for piece in smaller:
                results.extend(
                    await self._generate_chunk(
                        system_instruction=system_instruction,
                        payload=piece,
                        schema=schema,
                        request_id=request_id,
                        source_url=source_url,
                        record_type=record_type,
                    )
                )
            return results

    async def _generate_with_fallback(
        self,
        *,
        system_instruction: str,
        payload: str,
        schema: type[T],
        request_id: str | None,
        source_url: str | None,
        record_type: str | None,
    ) -> T:
        last_error: Exception | None = None

        for provider_index, provider_name in enumerate(
            self.settings.llm_provider_order
        ):
            provider = self.providers.get(provider_name)
            if provider is None:
                continue

            attempt_number = 0

            async def call_provider() -> T:
                nonlocal attempt_number
                attempt_number += 1
                started = time.perf_counter()

                try:
                    result = await provider.generate(
                        system_instruction, payload, schema
                    )
                except Exception as exc:
                    await self._record_attempt(
                        provider=provider,
                        status="error",
                        error=exc,
                        latency_ms=_elapsed_ms(started),
                        input_size_chars=len(payload),
                        estimated_tokens=estimate_tokens(payload),
                        output_size_chars=None,
                        retry_count=attempt_number - 1,
                        fallback_used=provider_index > 0,
                        request_id=request_id,
                        source_url=source_url,
                        record_type=record_type,
                    )
                    raise

                await self._record_attempt(
                    provider=provider,
                    status="success",
                    error=None,
                    latency_ms=_elapsed_ms(started),
                    input_size_chars=len(payload),
                    estimated_tokens=estimate_tokens(payload),
                    output_size_chars=len(result.model_dump_json()),
                    retry_count=attempt_number - 1,
                    fallback_used=provider_index > 0,
                    request_id=request_id,
                    source_url=source_url,
                    record_type=record_type,
                )
                return result

            try:
                return await retry_async(
                    call_provider,
                    max_attempts=max(1, self.settings.llm_max_retries + 1),
                    base_delay=self.settings.retry_base_delay_seconds,
                    max_delay=self.settings.retry_max_delay_seconds,
                    retryable_exceptions=(
                        RateLimitError,
                        NetworkError,
                        TimeoutErrorPipeline,
                        ParsingError,
                        ValidationError,
                    ),
                )
            except PayloadTooLargeError:
                raise
            except (
                RateLimitError,
                NetworkError,
                TimeoutErrorPipeline,
                ParsingError,
                ValidationError,
                PipelineError,
            ) as exc:
                last_error = exc
                continue

        raise ProviderUnavailableError(
            "All configured LLM providers were exhausted.",
            context={
                "provider_order": list(self.settings.llm_provider_order),
                "last_error_type": (
                    type(last_error).__name__ if last_error else None
                ),
            },
        ) from last_error

    async def _record_attempt(
        self,
        *,
        provider: LLMProvider,
        status: str,
        error: Exception | None,
        latency_ms: int,
        input_size_chars: int,
        estimated_tokens: int,
        output_size_chars: int | None,
        retry_count: int,
        fallback_used: bool,
        request_id: str | None,
        source_url: str | None,
        record_type: str | None,
    ) -> None:
        session_scope = self._session_scope_fn
        if session_scope is None:
            from src.storage.database import session_scope

        from src.storage.models import LLMRequest

        row = LLMRequest(
            provider=provider.provider_name,
            model=provider.model_name,
            request_id=request_id,
            source_url=source_url,
            record_type=record_type,
            input_size_chars=input_size_chars,
            estimated_tokens=estimated_tokens,
            output_size_chars=output_size_chars,
            latency_ms=latency_ms,
            status=status,
            retry_count=retry_count,
            fallback_used=fallback_used,
            error_type=type(error).__name__ if error else None,
        )

        async with session_scope() as session:
            session.add(row)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def build_orchestrator(
    *,
    gemini: LLMProvider | None = None,
    groq: LLMProvider | None = None,
    deepseek: LLMProvider | None = None,
    session_scope_fn: Callable | None = None,
) -> LLMOrchestrator:
    providers: dict[str, LLMProvider] = {}
    if gemini is not None:
        providers["gemini"] = gemini
    if groq is not None:
        providers["groq"] = groq
    if deepseek is not None:
        providers["deepseek"] = deepseek

    return LLMOrchestrator(
        providers,
        session_scope_fn=session_scope_fn,
    )
