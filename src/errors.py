"""
Typed error hierarchy.

Every error carries enough context (source, url, provider, status_code) to
debug without re-running the pipeline. Nothing here is swallowed silently by
callers -- see crawlers/base.py and llm/retry.py for how these are handled.
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline-raised exceptions."""

    def __init__(self, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}({self.message!r}, context={self.context!r})"


class NetworkError(PipelineError):
    """Connection-level failure (DNS, TCP reset, TLS, etc.)."""


class TimeoutErrorPipeline(PipelineError):
    """A network or provider call exceeded its configured timeout."""


class RateLimitError(PipelineError):
    """HTTP 429 or a provider-specific rate-limit signal.

    `retry_after_seconds` is populated when the server told us explicitly
    how long to wait (Retry-After header or provider equivalent).
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None, context: dict | None = None):
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class PayloadTooLargeError(PipelineError):
    """HTTP 413 or a provider token-limit rejection."""


class ParsingError(PipelineError):
    """Content could not be parsed into the expected structure."""


class ValidationError(PipelineError):
    """A record failed schema/quality validation."""


class AuthenticationError(PipelineError):
    """Missing or rejected credentials for a source or provider."""


class BlockedSourceError(PipelineError):
    """Source is actively blocking automated access (CAPTCHA, WAF, etc.).

    The pipeline records this and stops hitting the source -- it never
    attempts to bypass the block (see Section 31 anti-bot strategy).
    """


class ProviderUnavailableError(PipelineError):
    """All configured LLM providers in the fallback chain failed."""
