"""Jobs-only timestamp policy: explicit posting time, never update time."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from dateutil.parser import isoparse


def parse_job_timestamp(value: object) -> datetime | None:
    """Accept complete timezone-explicit ISO timestamps or aware datetimes.

    Date-only, relative, ambiguous, numeric, naive and unknown-offset (-00:00)
    values cannot prove a strict 24-hour posting window and are rejected.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})",
            value,
        ) or value.endswith(("-00:00", "-0000")):
            return None
        try:
            parsed = isoparse(value)
        except (ValueError, TypeError, OverflowError):
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
