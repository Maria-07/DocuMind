"""Heuristic flags for content that looks like instructions aimed at an AI model (prompt injection).

Flags are stored with each chunk and in the Qdrant payload. They do not block indexing — the
document is the user's data — but generation (Phase 4) treats flagged chunks with extra caution
and monitoring can surface them.
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all|any)\b.{0,20}\b(instructions?|prompts?|rules?)\b",
        r"\byou are now\b.{0,40}\b(assistant|ai|model|chatbot|dan)\b",
        r"\b(reveal|print|show|output)\b.{0,30}\b(system prompt|hidden instructions?|your instructions)\b",
        r"<\s*/?\s*(system|assistant|instructions?)\s*>",
        r"\bdo not (tell|inform) the user\b",
        r"\bnew instructions?\s*:",
    )
]


def content_flags(text: str) -> list[str]:
    flags: list[str] = []
    if any(p.search(text) for p in _INJECTION_PATTERNS):
        flags.append("possible_prompt_injection")
    return flags
