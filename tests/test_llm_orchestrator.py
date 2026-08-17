from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.errors import (
    AuthenticationError,
    NetworkError,
    PayloadTooLargeError,
    ProviderUnavailableError,
)
from src.llm.orchestrator import LLMOrchestrator
from src.llm.providers.base import LLMProvider


class Reply(BaseModel):
    text: str


class StubProvider(LLMProvider):
    def __init__(self, name: str, outcomes: list[Reply | Exception]) -> None:
        self._name = name
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return f"{self._name}-model"

    async def generate(
        self,
        system_instruction: str,
        user_payload: str,
        schema: type[Reply],
    ) -> Reply:
        self.calls.append(user_payload)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RejectLargeProvider(StubProvider):
    async def generate(
        self,
        system_instruction: str,
        user_payload: str,
        schema: type[Reply],
    ) -> Reply:
        self.calls.append(user_payload)
        if len(user_payload) > 2:
            raise PayloadTooLargeError("payload too large")
        return Reply(text=user_payload)


def make_orchestrator(
    providers: dict[str, LLMProvider],
    recorded_rows: list[object],
    provider_order: list[str],
    *,
    max_retries: int = 0,
) -> LLMOrchestrator:
    @asynccontextmanager
    async def session_scope():
        yield SimpleNamespace(add=recorded_rows.append)

    orchestrator = LLMOrchestrator(
        providers,
        session_scope_fn=session_scope,
    )
    orchestrator.settings = SimpleNamespace(
        llm_token_budget=1,
        llm_provider_order=provider_order,
        llm_max_retries=max_retries,
        retry_base_delay_seconds=0,
        retry_max_delay_seconds=0,
    )
    return orchestrator


@pytest.mark.asyncio
async def test_empty_payload_returns_empty_list() -> None:
    provider = StubProvider("gemini", [Reply(text="unused")])
    orchestrator = make_orchestrator({"gemini": provider}, [], ["gemini"])

    assert await orchestrator.generate("system", "", Reply) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_success_records_attempt() -> None:
    rows: list[object] = []
    provider = StubProvider("gemini", [Reply(text="done")])
    orchestrator = make_orchestrator({"gemini": provider}, rows, ["gemini"])

    result = await orchestrator.generate(
        "system", "work", Reply, request_id="req-1"
    )

    assert result == Reply(text="done")
    assert len(rows) == 1
    assert rows[0].provider == "gemini"
    assert rows[0].status == "success"
    assert rows[0].request_id == "req-1"
    assert rows[0].fallback_used is False


@pytest.mark.asyncio
async def test_retryable_failure_falls_back() -> None:
    rows: list[object] = []
    primary = StubProvider("gemini", [NetworkError("unavailable")])
    fallback = StubProvider("groq", [Reply(text="recovered")])

    orchestrator = make_orchestrator(
        {"gemini": primary, "groq": fallback},
        rows,
        ["gemini", "groq"],
    )

    result = await orchestrator.generate("system", "work", Reply)

    assert result == Reply(text="recovered")
    assert [row.status for row in rows] == ["error", "success"]
    assert rows[1].fallback_used is True


@pytest.mark.asyncio
async def test_413_rechunks_without_retrying_same_payload() -> None:
    rows: list[object] = []
    provider = RejectLargeProvider("gemini", [])
    orchestrator = make_orchestrator({"gemini": provider}, rows, ["gemini"])

    result = await orchestrator.generate("system", "abcd", Reply)

    assert result == [Reply(text="ab"), Reply(text="cd")]
    assert provider.calls == ["abcd", "ab", "cd"]
    assert [row.status for row in rows] == [
        "error",
        "success",
        "success",
    ]


@pytest.mark.asyncio
async def test_authentication_error_is_not_fallback() -> None:
    rows: list[object] = []
    primary = StubProvider(
        "gemini",
        [AuthenticationError("unauthorized")],
    )
    fallback = StubProvider("groq", [Reply(text="should-not-run")])

    orchestrator = make_orchestrator(
        {"gemini": primary, "groq": fallback},
        rows,
        ["gemini", "groq"],
    )

    with pytest.raises(AuthenticationError):
        await orchestrator.generate("system", "work", Reply)

    assert fallback.calls == []


@pytest.mark.asyncio
async def test_all_providers_exhausted() -> None:
    rows: list[object] = []
    gemini = StubProvider("gemini", [NetworkError("down")])
    groq = StubProvider("groq", [NetworkError("down")])

    orchestrator = make_orchestrator(
        {"gemini": gemini, "groq": groq},
        rows,
        ["gemini", "groq"],
    )

    with pytest.raises(ProviderUnavailableError):
        await orchestrator.generate("system", "work", Reply)

    assert len(rows) == 2
    assert all(row.status == "error" for row in rows)
