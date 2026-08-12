from __future__ import annotations

import abc
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(abc.ABC):
    """Generic interface for all LLM providers."""

    @abc.abstractmethod
    async def generate(self, system_instruction: str, user_payload: str, schema: type[T]) -> T:
        """
        Generate structured output from the LLM.

        Args:
            system_instruction: Defines behavior and rules.
            user_payload: The raw text/chunk.
            schema: Pydantic model enforcing structured output.

        Returns:
            An instance of `schema` containing the validated output.

        Raises:
            ParsingError: If structured JSON cannot be parsed.
            ValidationError: If the JSON violates the requested schema.
            RateLimitError: For HTTP 429 or rate-limit signals.
            PayloadTooLargeError: For HTTP 413.
            AuthenticationError: For 401 or non-rate-limit 403.
            NetworkError / TimeoutErrorPipeline / PipelineError: For other network issues.
        """
        pass
