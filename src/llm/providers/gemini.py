import json

from pydantic import BaseModel, ValidationError as PydanticValidationError

from src.config.settings import get_settings
from src.crawlers.http import AsyncHttpClient
from src.errors import AuthenticationError, ParsingError, ValidationError
from src.llm.providers.base import LLMProvider, T


class GeminiProvider(LLMProvider):
    """Gemini LLM Provider Adapter."""

    def __init__(self, http_client: AsyncHttpClient):
        self._http_client = http_client
        self._settings = get_settings()

    async def generate(self, system_instruction: str, user_payload: str, schema: type[T]) -> T:
        if not self._settings.gemini_api_key or not self._settings.gemini_model:
            raise AuthenticationError("Gemini API key or model not configured.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._settings.gemini_model}:generateContent?key={self._settings.gemini_api_key}"
        
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_payload}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        # retry=False because retry orchestration belongs to Task 3 (orchestrator)
        result = await self._http_client.post(url, json=payload, retry=False)

        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError("Provider returned malformed JSON wrapper") from exc

        try:
            # Extract JSON string from Gemini response format
            text_response = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ParsingError("Provider response did not match expected structure") from exc

        try:
            parsed_json = json.loads(text_response)
        except json.JSONDecodeError as exc:
            raise ParsingError("Provider generated malformed JSON payload") from exc

        try:
            return schema.model_validate(parsed_json)
        except PydanticValidationError as exc:
            raise ValidationError("Provider generated JSON failed schema validation") from exc
