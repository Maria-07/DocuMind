"""Decides, per chat message, whether the assistant needs the organization's documents.

Without this step every message went through document search, so "hello" matched a few unrelated
chunks and got an answer built from them. Now:

* an unmistakable greeting / thanks / goodbye is recognised by rule, with no model call;
* anything else is classified by one small structured-output call, which also rewrites a follow-up
  ("and for contractors?") into a standalone search query;
* if that call fails, the message is searched in the documents, exactly as before. Searching and
  finding nothing is harmless; skipping a search the user needed is not.

Routing only chooses *how to answer*. A "chat" reply has no document access at all, so a
misclassification can cost an answer's grounding but can never leak a document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from html import escape
from typing import Any

import structlog

from app.rag.generation.base import ChatTurn, GenerationError, LLMProvider
from app.rag.generation.prompts import ROUTER_SYSTEM_PROMPT

log = structlog.get_logger(__name__)

_HISTORY_TURNS = 6
_TURN_CHARS = 600


class Route(StrEnum):
    CHAT = "chat"  # no documents needed: greeting, thanks, "what can you do?", general help
    DOCUMENTS = "documents"  # search the documents and answer with citations


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    query: str  # standalone search query for DOCUMENTS; empty for CHAT
    source: str  # "rule" | "model" | "fallback"


# --- Rule-based fast path ---------------------------------------------------------------------------
# Whole-message matches only. "thanks, now summarise the policy" is not in the set, so it is classified
# by the model. Anything not listed here (other languages, "how are you?") also goes to the model.
_SOCIAL = frozenset(
    {
        # greetings
        "hi", "hello", "hey", "heya", "hiya", "howdy", "yo", "greetings",
        "good morning", "good afternoon", "good evening", "good day",
        # thanks
        "thanks", "thank you", "thx", "ty", "cheers", "many thanks", "much appreciated",
        "thanks a lot", "thanks so much", "thank you so much", "thank you very much",
        "thanks again", "thank you again", "great thanks", "ok thanks", "okay thanks",
        "perfect thanks", "awesome thanks", "got it thanks", "thanks got it",
        # acknowledgements
        "ok", "okay", "k", "great", "perfect", "awesome", "nice", "cool", "got it", "understood",
        "sounds good", "i see", "alright", "ok great", "ok cool", "okay cool", "ok got it", "okay got it",
        # goodbyes
        "bye", "goodbye", "bye bye", "see you", "see you later", "see ya", "good night", "cya", "later",
    }
)  # fmt: skip
_ADDRESSEE = frozenset({"documind", "assistant", "bot", "there", "team", "everyone", "all"})
_NOT_WORD = re.compile(r"[^\w\s']+")
_LONG_RUN = re.compile(r"(.)\1{2,}")  # "heyyy" -> "hey", "thankssss" -> "thanks"
_MAX_QUICK_WORDS = 5


def quick_route(message: str) -> RouteDecision | None:
    """CHAT for an unmistakable greeting / thanks / acknowledgement / goodbye; otherwise None."""
    text = _NOT_WORD.sub(" ", message.lower())
    words = _LONG_RUN.sub(r"\1", text).split()
    if not words or len(words) > _MAX_QUICK_WORDS:
        return None
    while len(words) > 1 and words[-1] in _ADDRESSEE:  # "hello there", "thanks DocuMind"
        words.pop()
    if " ".join(words) in _SOCIAL:
        return RouteDecision(Route.CHAT, "", "rule")
    return None


# --- Model classification ---------------------------------------------------------------------------
def router_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": [r.value for r in Route]},
            "search_query": {"type": "string"},
        },
        "required": ["route", "search_query"],
        "additionalProperties": False,
    }


def router_prompt(message: str, history: list[ChatTurn], *, scoped: bool) -> str:
    turns = [f"{t.role}: {t.content[:_TURN_CHARS]}" for t in history[-_HISTORY_TURNS:] if t.content.strip()]
    parts = []
    if turns:
        parts.append("<conversation>\n" + escape("\n".join(turns), quote=False) + "\n</conversation>")
    if scoped:
        parts.append(
            "(The user is chatting about specific documents they selected, so questions about their "
            'content belong to "documents".)'
        )
    parts.append(f"<latest_message>{escape(message, quote=False)}</latest_message>")
    if turns:
        parts.append("(Resolve any reference to the conversation in the search query.)")
    return "\n".join(parts)


async def route_message(
    llm: LLMProvider,
    message: str,
    history: list[ChatTurn],
    *,
    scoped: bool = False,
    usage_out: dict[str, Any] | None = None,
) -> RouteDecision:
    quick = quick_route(message)
    if quick is not None:
        return quick
    try:
        raw = await llm.complete_json(
            system=ROUTER_SYSTEM_PROMPT,
            prompt=router_prompt(message, history, scoped=scoped),
            schema=router_schema(),
            name="route_message",
            max_tokens=2000,  # reasoning tokens count toward this limit
            effort="low",
            usage_out=usage_out,
        )
    except GenerationError as exc:
        log.warning("routing_failed", code=exc.code)
        return RouteDecision(Route.DOCUMENTS, message, "fallback")
    if raw.get("route") == Route.CHAT.value:
        return RouteDecision(Route.CHAT, "", "model")
    query = str(raw.get("search_query") or "").strip().strip('"')[:1000]
    return RouteDecision(Route.DOCUMENTS, query or message, "model")
