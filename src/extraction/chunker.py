"""
Deterministic text chunker for the LLM orchestration engine.

Splits text payloads that exceed the configured token budget using a
simple character-count heuristic.  The chunker guarantees:

* Ordering is preserved.
* No text is silently dropped or duplicated.
* Concatenating chunks in order reproduces the original payload exactly.
* No network calls or provider-specific tokenizers are used.
* Empty payloads return zero chunks.

Token estimation
~~~~~~~~~~~~~~~~
``estimated_tokens = math.ceil(len(text) / 4)``

This is a *rough* approximation for budget/chunking purposes only — it
is never represented as exact provider billing usage.
"""
from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: ``ceil(len(text) / 4)``.

    * Empty string → 0
    * Deterministic — same input always produces the same result.
    * No network calls; no provider-specific tokenizer dependency.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def chunk_text(text: str, *, char_budget: int) -> list[str]:
    """Split *text* into chunks that each fit within *char_budget* characters.

    ``char_budget`` is the character budget corresponding to the token
    budget: ``llm_token_budget * 4`` (inverse of the ``/ 4`` heuristic).

    Behaviour
    ---------
    * If ``text`` is empty, return ``[]``.
    * If ``len(text) <= char_budget``, return ``[text]`` unchanged.
    * Otherwise, split at newline boundaries where possible. If a single
      line exceeds ``char_budget`` it is further split at character
      boundaries.
    * Concatenating the returned chunks reproduces the original text
      exactly.

    Returns
    -------
    list[str]
        Ordered list of text chunks.
    """
    if not text:
        return []
    if char_budget <= 0:
        char_budget = 1
    if len(text) <= char_budget:
        return [text]

    # Split into lines, keeping the newline terminators so we can
    # reconstruct the original text by simple concatenation.
    lines = _split_keeping_newlines(text)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line)

        if line_len > char_budget:
            # Flush the current accumulator first.
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            # Split the oversized line into char-boundary pieces.
            for piece in _split_by_chars(line, char_budget):
                chunks.append(piece)
        elif current_len + line_len > char_budget:
            # Adding this line would exceed the budget → flush.
            chunks.append("".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("".join(current))

    return chunks


def _split_keeping_newlines(text: str) -> list[str]:
    """Split *text* into segments, each terminated by its newline (if any).

    The final segment may or may not have a trailing newline, depending on
    the original text.  Concatenating all segments reproduces *text*.
    """
    parts: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch == "\n":
            parts.append(text[start : i + 1])
            start = i + 1
    if start < len(text):
        parts.append(text[start:])
    return parts


def _split_by_chars(text: str, size: int) -> list[str]:
    """Split *text* into pieces of at most *size* characters."""
    return [text[i : i + size] for i in range(0, len(text), size)]
