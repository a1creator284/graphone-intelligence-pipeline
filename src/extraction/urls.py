"""
URL normalization for deduplication (Section 28).

Strips tracking parameters, trailing slashes, fragments, and normalizes
casing/encoding where it's safe to do so without changing the resource the
URL identifies.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM_PREFIXES = ("utm_", "ga_", "mc_", "icid", "fbclid", "gclid", "ref", "ref_src", "igshid", "spm")


def _is_tracking_param(key: str) -> bool:
    key_lower = key.lower()
    return any(key_lower == p or key_lower.startswith(p) for p in _TRACKING_PARAM_PREFIXES)


def normalize_url(raw_url: str) -> str:
    """Produce a canonical form of a URL suitable for dedup comparisons.

    - lowercase scheme + host (path casing is left alone -- it can be
      case-sensitive on the server)
    - drop the fragment
    - drop known tracking query params, keep the rest sorted for stability
    - drop a single trailing slash on the path (but not for the bare root "/")
    """
    parts = urlsplit(raw_url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking_param(k)]
    query_pairs.sort()
    query = urlencode(query_pairs)

    return urlunsplit((scheme, netloc, path, query, ""))
