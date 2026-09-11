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
        llm_max_split_depth=8,
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
async def test_authentication_error_falls_back_without_retry() -> None:
    rows: list[object] = []
    primary = StubProvider(
        "gemini",
        [AuthenticationError("unauthorized")],
    )
    fallback = StubProvider("groq", [Reply(text="recovered")])

    orchestrator = make_orchestrator(
        {"gemini": primary, "groq": fallback},
        rows,
        ["gemini", "groq"],
    )

    assert await orchestrator.generate("system", "work", Reply) == Reply(text="recovered")
    assert primary.calls == ["work"]
    assert fallback.calls == ["work"]
    assert rows[0].error_type == "AuthenticationError"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("successful_index", [0, 1, 2, None])
async def test_default_three_provider_order_and_honest_exhaustion(successful_index):
    from src.config.settings import Settings

    order = Settings(_env_file=None).llm_provider_order
    assert order == ["gemini", "groq", "deepseek"]
    rows = []
    # Deliberately reverse dictionary insertion order: configuration controls execution.
    providers = {
        name: StubProvider(name, [Reply(text=name) if index == successful_index else NetworkError("down")])
        for index, name in reversed(list(enumerate(order)))
    }
    orchestrator = make_orchestrator(providers, rows, order)
    if successful_index is None:
        with pytest.raises(ProviderUnavailableError) as error:
            await orchestrator.generate("system", "work", Reply)
        assert isinstance(error.value.__cause__, NetworkError)
        assert error.value.context["provider_order"] == order
        assert all(row.status == "error" for row in rows)
        attempted = order
    else:
        assert await orchestrator.generate("system", "work", Reply) == Reply(text=order[successful_index])
        attempted = order[:successful_index + 1]
        assert [row.status for row in rows] == ["error"] * successful_index + ["success"]
    assert [row.provider for row in rows] == attempted
    assert [row.fallback_used for row in rows] == [i > 0 for i in range(len(attempted))]
    for name in order:
        assert providers[name].calls == (["work"] if name in attempted else [])


@pytest.mark.asyncio
async def test_all_missing_credentials_fail_honestly():
    rows = []
    order = ["gemini", "groq", "deepseek"]
    providers = {name: StubProvider(name, [AuthenticationError("not configured")]) for name in order}
    orchestrator = make_orchestrator(providers, rows, order, max_retries=3)
    with pytest.raises(ProviderUnavailableError):
        await orchestrator.generate("system", "work", Reply)
    assert [row.provider for row in rows] == order
    assert all(row.error_type == "AuthenticationError" and row.retry_count == 0 for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,depth,expected", [("x", 8, ["x"]), ("abcdefgh", 1, ["abcdefgh", "abcd"])])
async def test_impossible_413_fails_at_single_character_or_depth_bound(payload, depth, expected):
    rows = []
    provider = StubProvider("gemini", [PayloadTooLargeError("413")] * len(expected))
    orchestrator = make_orchestrator({"gemini": provider}, rows, ["gemini"], max_retries=3)
    orchestrator.settings.llm_token_budget = 100
    orchestrator.settings.llm_max_split_depth = depth
    with pytest.raises(PayloadTooLargeError):
        await orchestrator.generate("oversized system instruction", payload, Reply)
    assert provider.calls == expected
    assert all(row.status == "error" for row in rows)


@pytest.mark.asyncio
async def test_exact_duplicate_results_removed_in_first_seen_order():
    rows = []
    provider = StubProvider("gemini", [Reply(text="first"), Reply(text="second"), Reply(text="first")])
    orchestrator = make_orchestrator({"gemini": provider}, rows, ["gemini"])
    result = await orchestrator.generate("system", "abcdefghijkl", Reply)
    assert result == [Reply(text="first"), Reply(text="second")]
    assert provider.calls == ["abcd", "efgh", "ijkl"]
    assert len(rows) == 3  # attempts are never deduplicated from telemetry


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_last", [False, True])
async def test_http_413_roundtrip_order_no_replay_and_no_partial_success(respx_mock, monkeypatch, fail_last):
    import json
    import httpx
    from src.config.settings import get_settings
    from src.crawlers.http import AsyncHttpClient
    from src.llm.providers.groq import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setenv("GROQ_MODEL", "test-llama")
    get_settings.cache_clear()
    payloads = []

    def respond(request):
        payload = json.loads(request.content)["messages"][1]["content"]
        payloads.append(payload)
        if len(payload) > 2 or (fail_last and payload == "gh"):
            return httpx.Response(413)
        if fail_last and payload == "h":
            return httpx.Response(413)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"text": payload})}}]})

    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=respond)
    try:
        async with AsyncHttpClient() as client:
            rows = []
            orchestrator = make_orchestrator({"groq": GroqProvider(client)}, rows, ["groq"], max_retries=3)
            orchestrator.settings.llm_token_budget = 100
            if fail_last:
                with pytest.raises(PayloadTooLargeError):
                    await orchestrator.generate("system", "abcdefgh", Reply)
                assert payloads == ["abcdefgh", "abcd", "ab", "cd", "efgh", "ef", "gh", "g", "h"]
            else:
                results = await orchestrator.generate("system", "abcdefgh", Reply)
                assert [result.text for result in results] == ["ab", "cd", "ef", "gh"]
                assert "".join(result.text for result in results) == "abcdefgh"
                assert payloads == ["abcdefgh", "abcd", "ab", "cd", "efgh", "ef", "gh"]
            assert len(set(payloads)) == len(payloads)
            assert len(rows) == len(payloads)
    finally:
        get_settings.cache_clear()
