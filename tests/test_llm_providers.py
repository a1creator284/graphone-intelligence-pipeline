import json
import pytest
from pydantic import BaseModel, Field

from src.crawlers.http import AsyncHttpClient
from src.errors import (
    AuthenticationError,
    ParsingError,
    ValidationError,
    RateLimitError,
    PayloadTooLargeError,
    NetworkError,
    TimeoutErrorPipeline,
)
from src.llm.providers.gemini import GeminiProvider
from src.llm.providers.groq import GroqProvider
from src.llm.providers.deepseek import DeepSeekProvider
import httpx

class DummySchema(BaseModel):
    name: str
    age: int

@pytest.fixture
def http_client():
    return AsyncHttpClient()

from src.config.settings import get_settings

@pytest.fixture
def configure_providers(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
    monkeypatch.setenv("GROQ_API_KEY", "test_groq_key")
    monkeypatch.setenv("GROQ_MODEL", "llama3-groq")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test_deepseek_key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_gemini_provider_success(respx_mock, http_client, configure_providers):
    gemini_resp = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps({"name": "Alice", "age": 30})}]}}
        ]
    }
    respx_mock.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=test_gemini_key").mock(
        return_value=httpx.Response(200, json=gemini_resp)
    )
    provider = GeminiProvider(http_client)
    res = await provider.generate("sys", "user", DummySchema)
    assert res.name == "Alice"
    assert res.age == 30

@pytest.mark.asyncio
async def test_gemini_missing_config(http_client, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = GeminiProvider(http_client)
    with pytest.raises(AuthenticationError):
        await provider.generate("sys", "user", DummySchema)
    get_settings.cache_clear()

@pytest.mark.asyncio
async def test_groq_provider_success(respx_mock, http_client, configure_providers):
    groq_resp = {
        "choices": [
            {"message": {"content": json.dumps({"name": "Bob", "age": 25})}}
        ]
    }
    req = respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=groq_resp)
    )
    provider = GroqProvider(http_client)
    res = await provider.generate("sys", "user", DummySchema)
    assert res.name == "Bob"
    assert res.age == 25
    assert req.calls.last.request.headers["authorization"] == "Bearer test_groq_key"

@pytest.mark.asyncio
async def test_deepseek_provider_success(respx_mock, http_client, configure_providers):
    deepseek_resp = {
        "choices": [
            {"message": {"content": json.dumps({"name": "Charlie", "age": 40})}}
        ]
    }
    respx_mock.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, json=deepseek_resp)
    )
    provider = DeepSeekProvider(http_client)
    res = await provider.generate("sys", "user", DummySchema)
    assert res.name == "Charlie"
    assert res.age == 40

@pytest.mark.asyncio
async def test_provider_malformed_json_wrapper(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="not json")
    )
    provider = GroqProvider(http_client)
    with pytest.raises(ParsingError, match="malformed JSON wrapper"):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_malformed_json_payload(respx_mock, http_client, configure_providers):
    groq_resp = {
        "choices": [
            {"message": {"content": "{not valid json}"}}
        ]
    }
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=groq_resp)
    )
    provider = GroqProvider(http_client)
    with pytest.raises(ParsingError, match="malformed JSON payload"):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_schema_validation_error(respx_mock, http_client, configure_providers):
    groq_resp = {
        "choices": [
            {"message": {"content": json.dumps({"name": "Bob", "age": "not an int"})}}
        ]
    }
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=groq_resp)
    )
    provider = GroqProvider(http_client)
    with pytest.raises(ValidationError, match="failed schema validation"):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_429_rate_limit(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"})
    )
    provider = GroqProvider(http_client)
    with pytest.raises(RateLimitError):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_413_payload_too_large(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(413)
    )
    provider = GroqProvider(http_client)
    with pytest.raises(PayloadTooLargeError):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_401_auth_error(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(401)
    )
    provider = GroqProvider(http_client)
    with pytest.raises(AuthenticationError):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_5xx_server_error(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )
    provider = GroqProvider(http_client)
    with pytest.raises(NetworkError):
        await provider.generate("sys", "user", DummySchema)

@pytest.mark.asyncio
async def test_provider_timeout_error(respx_mock, http_client, configure_providers):
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    provider = GroqProvider(http_client)
    with pytest.raises(TimeoutErrorPipeline):
        await provider.generate("sys", "user", DummySchema)
