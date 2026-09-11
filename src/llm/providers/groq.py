import json

from pydantic import BaseModel, ValidationError as PydanticValidationError

from src.config.settings import get_settings
from src.crawlers.http import AsyncHttpClient
from src.errors import AuthenticationError, ParsingError, ValidationError
from src.llm.providers.base import LLMProvider, T


class GroqProvider(LLMProvider):
    """Groq LLM Provider Adapter."""

    def __init__(self, http_client: AsyncHttpClient):
        self._http_client = http_client
        self._settings = get_settings()

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._settings.groq_model or ""

    async def generate(self, system_instruction: str, user_payload: str, schema: type[T]) -> T:
        if not self._settings.groq_api_key or not self._settings.groq_model:
            raise AuthenticationError("Groq API key or model not configured.")

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.groq_api_key}",
        }
        
        schema_json = json.dumps(schema.model_json_schema())
        augmented_system_instruction = f"{system_instruction}\n\nYou MUST respond in strictly valid JSON matching this schema:\n{schema_json}"

        payload = {
            "model": self._settings.groq_model,
            "messages": [
                {"role": "system", "content": augmented_system_instruction},
                {"role": "user", "content": user_payload}
            ],
            "response_format": {"type": "json_object"}
        }

        # retry=False because retry orchestration belongs to Task 3 (orchestrator)
        result = await self._http_client.post(url, headers=headers, json=payload, retry=False)

        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ParsingError("Provider returned malformed JSON wrapper") from exc

        try:
            # Extract JSON string from OpenAI-compatible response format
            text_response = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ParsingError("Provider response did not match expected structure") from exc

        try:
            parsed_json = json.loads(text_response)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ParsingError("Provider generated malformed JSON payload") from exc

        try:
            return schema.model_validate(parsed_json)
        except PydanticValidationError as exc:
            raise ValidationError("Provider generated JSON failed schema validation") from exc
