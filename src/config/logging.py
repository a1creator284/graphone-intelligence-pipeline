"""
Structured logging setup.

Every log line is a structured event (JSON in production, readable console
output in development) carrying consistent fields: timestamp, level, worker,
source, url, record_type, operation, duration, status, error_category.
"""
from __future__ import annotations

import logging
import sys

import structlog

from src.config.settings import get_settings

_SENSITIVE_KEYS = {"api_key", "token", "password", "authorization", "secret"}


def _redact_sensitive(_logger, _method_name, event_dict):
    """Never let secrets or full prompts leak into logs."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***redacted***"
    prompt = event_dict.get("prompt")
    if isinstance(prompt, str) and len(prompt) > 200:
        event_dict["prompt"] = prompt[:200] + "...(truncated)"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_context):
    return structlog.get_logger(**initial_context)
