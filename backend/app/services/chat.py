"""Conversations and grounded, streamed answers with citations.

Streaming flow for one message (events in brackets are sent to the client):

    [meta] [status: thinking] → route the message (app.rag.routing)
      · chat      (greeting, thanks, "what can you do?", general help)
                  [status: generating] → LLM stream → [token]*
      · documents [status: retrieving] → retrieve (standalone query) → [sources] → [status: generating]
                  → LLM stream → [token]* / [citation]*
    → persist answer (+ citations + retrieval log) → [done]               (or [error] at any point)

A "chat" reply has no document access at all. For document questions, if retrieval finds nothing
relevant enough the model is not called and the answer says so.
"""

from __future__ import annotations

import asyncio
import builtins
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import NotFoundError, ServiceUnavailableError, ValidationFailedError
from app.core.permissions import Principal
from app.models import Conversation, Message, MessageCitation
from app.rag.answering import (
    NO_ANSWER_TEXT,
    REFUSAL_TEXT,
    AnswerToken,
    Citation,
    CitationTracker,
    NewCitation,
    to_source_documents,
)
from app.rag.generation.base import ChatTurn, GenerationDone, GenerationError, TextDelta
from app.rag.generation.catalog import merge_usage, today
from app.rag.generation.registry import ModelRegistry
from app.rag.retrieval import RetrievalFilters, RetrievedChunk
from app.rag.routing import Route, route_message
from app.rag.runtime import RagRuntime
from app.schemas.chat import (
    ChatModelRead,
    ChatModelsResponse,
    CitationRead,
    ConversationCreate,
    ConversationUpdate,
    MessageRead,
    ModelPriceRead,
    SourceRead,
)
from app.schemas.common import PageParams
from app.services.search import log_retrieval

log = structlog.get_logger(__name__)

DEFAULT_TITLE = "New conversation"


def _title_for(question: str) -> str:
    return question[:80] + ("…" if len(question) > 80 else "")


def _before(m: Message) -> ColumnElement[bool]:
    """Messages created strictly before ``m`` (conversation order is created_at, then id)."""
    return or_(Message.created_at < m.created_at, and_(Message.created_at == m.created_at, Message.id < m.id))


def _from(m: Message) -> ColumnElement[bool]:
    """``m`` and every message created after it."""
    return or_(Message.created_at > m.created_at, and_(Message.created_at == m.created_at, Message.id >= m.id))


def usage_record(
    models: ModelRegistry,
    *,
    answer_model: str,
    answer_usage: dict[str, Any],
    served_model: str | None,
    router_model: str,
    routing_usage: dict[str, Any],
) -> dict[str, Any]:
    """Token usage and estimated cost of one reply, stored with the message.

    Totals cover every model call made for the message (the routing call and the answer). ``cost_usd`` is None
    when any call's model has no known price. Prices are applied as of today, then frozen with the message.
    """
    total = merge_usage(answer_usage, routing_usage)
    if not total:
        return {}
    record: dict[str, Any] = dict(total)
    costs: list[float | None] = []
    for step, model, usage in (("routing", router_model, routing_usage), ("answer", answer_model, answer_usage)):
        if not usage:
            continue
        cost = models.cost(model, usage)
        costs.append(cost)
        record[step] = {"model": model, **merge_usage(usage), "cost_usd": cost}
    if served_model and "answer" in record:
        record["answer"]["model_version"] = served_model  # the provider's exact snapshot name
    record["cost_usd"] = None if any(c is None for c in costs) else sum(c for c in costs if c is not None)
    return record


@dataclass(frozen=True, slots=True)
class ChatEvent:
    name: str  # meta | status | sources | token | citation | done | error
    data: dict[str, Any]


@dataclass(slots=True)
class PreparedAsk:
    principal: Principal
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    question: str
    history: builtins.list[ChatTurn]
    filters: RetrievalFilters
    model_id: str
    started: float = field(default_factory=time.perf_counter)


def citation_url(document_id: uuid.UUID | None, chunk_id: uuid.UUID | None) -> str | None:
    if document_id is None:
        return None
    return f"/documents/{document_id}" + (f"?chunk={chunk_id}" if chunk_id else "")


def citation_read(c: Citation) -> CitationRead:
    ch = c.chunk
    return CitationRead(
        index=c.number,
        chunk_id=ch.chunk_id,
        document_id=ch.document_id,
        document_title=ch.document_title,
        page_start=ch.page_start,
        page_end=ch.page_end,
        sheet_name=ch.sheet_name,
        row_start=ch.row_start,
        row_end=ch.row_end,
        section_path=ch.section_path,
        snippet=c.snippet,
        score=ch.relevance,
        url=citation_url(ch.document_id, ch.chunk_id),
    )


def citation_read_from_row(row: MessageCitation) -> CitationRead:
    return CitationRead(
        index=row.citation_index,
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        document_title=row.document_title,
        page_start=row.page_start,
        page_end=row.page_end,
        sheet_name=row.sheet_name,
        row_start=row.row_start,
        row_end=row.row_end,
        section_path=row.section_path,
        snippet=row.cited_text,
        score=row.score,
        url=citation_url(row.document_id, row.chunk_id),
    )


def source_read(c: RetrievedChunk) -> SourceRead:
    return SourceRead(
        chunk_id=c.chunk_id,
        document_id=c.document_id,
        document_title=c.document_title,
        location=c.location,
        page_start=c.page_start,
        sheet_name=c.sheet_name,
        section_path=c.section_path,
        snippet=c.text[:300],
        relevance=c.relevance,
    )


class ChatService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, rag: RagRuntime) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.rag = rag

    # =======================================================================================
    # Conversations
    # =======================================================================================
    async def _owned(self, s: AsyncSession, principal: Principal, conversation_id: uuid.UUID) -> Conversation:
        conv = await s.get(Conversation, conversation_id)
        if (
            conv is None
            or conv.deleted_at is not None
            or conv.user_id != principal.user_id
            or conv.organization_id != principal.organization_id
        ):
            raise NotFoundError("Conversation not found", code="conversation_not_found")
        return conv

    async def create(self, principal: Principal, data: ConversationCreate) -> Conversation:
        async with self.sessionmaker() as s:
            conv = Conversation(
                organization_id=principal.organization_id,
                user_id=principal.user_id,
                title=(data.title or "").strip() or DEFAULT_TITLE,
                scope=data.scope.as_dict() if data.scope else {},
            )
            s.add(conv)
            await s.commit()
            return conv

    async def list(self, principal: Principal, page: PageParams) -> tuple[builtins.list[Conversation], int]:
        conditions = (
            Conversation.user_id == principal.user_id,
            Conversation.organization_id == principal.organization_id,
            Conversation.deleted_at.is_(None),
        )
        async with self.sessionmaker() as s:
            total = await s.scalar(select(func.count()).select_from(Conversation).where(*conditions))
            rows = (
                (
                    await s.execute(
                        select(Conversation)
                        .where(*conditions)
                        .order_by(Conversation.updated_at.desc())
                        .offset(page.offset)
                        .limit(page.page_size)
                    )
                )
                .scalars()
                .all()
            )
        return list(rows), int(total or 0)

    async def get(
        self, principal: Principal, conversation_id: uuid.UUID
    ) -> tuple[Conversation, builtins.list[MessageRead]]:
        async with self.sessionmaker() as s:
            conv = await self._owned(s, principal, conversation_id)
            messages = (
                (
                    await s.execute(
                        select(Message)
                        .where(Message.conversation_id == conv.id, Message.superseded_at.is_(None))
                        .order_by(Message.created_at, Message.id)
                    )
                )
                .scalars()
                .all()
            )
            citations: dict[uuid.UUID, builtins.list[MessageCitation]] = {}
            if messages:
                for row in (
                    await s.execute(
                        select(MessageCitation)
                        .where(MessageCitation.message_id.in_([m.id for m in messages]))
                        .order_by(MessageCitation.citation_index)
                    )
                ).scalars():
                    citations.setdefault(row.message_id, []).append(row)
        return conv, [
            MessageRead.model_validate(m).model_copy(
                update={"citations": [citation_read_from_row(c) for c in citations.get(m.id, [])]}
            )
            for m in messages
        ]

    async def update(self, principal: Principal, conversation_id: uuid.UUID, data: ConversationUpdate) -> Conversation:
        async with self.sessionmaker() as s:
            conv = await self._owned(s, principal, conversation_id)
            changes = data.model_dump(exclude_unset=True)
            if changes.get("title"):
                conv.title = changes["title"].strip()
            if "scope" in changes:
                conv.scope = data.scope.as_dict() if data.scope else {}
            await s.commit()
            return conv

    async def delete(self, principal: Principal, conversation_id: uuid.UUID) -> None:
        async with self.sessionmaker() as s:
            conv = await self._owned(s, principal, conversation_id)
            conv.deleted_at = datetime.now(UTC)
            await s.commit()

    # =======================================================================================
    # Asking
    # =======================================================================================
    def list_models(self) -> ChatModelsResponse:
        """The models the user can pick (those whose provider key is configured) with today's prices."""
        day = today()
        models = []
        for option in self.rag.models.available():
            price = option.price(day)
            models.append(
                ChatModelRead(
                    id=option.spec.id,
                    label=option.spec.label,
                    provider=option.spec.provider,
                    description=option.spec.description,
                    is_default=option.is_default,
                    unavailable_reason=option.unavailable_reason,
                    price=ModelPriceRead(input=price.input, output=price.output, cached_input=price.cached_input)
                    if price
                    else None,
                )
            )
        return ChatModelsResponse(default=self.rag.models.default_id, models=models)

    async def prepare_ask(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        content: str,
        scope_override: dict[str, Any] | None = None,
        *,
        model_id: str | None = None,
        edit_message_id: uuid.UUID | None = None,
    ) -> PreparedAsk:
        """Validate and persist the user message *before* streaming starts (so errors are proper HTTP).

        With ``edit_message_id`` the user is editing one of their earlier messages: that message and everything
        after it is marked superseded (hidden, not deleted) and the new message takes its place, with only the
        messages *before* the edited one as context.
        """
        models = self.rag.models
        default_id = models.default_id
        if default_id is None:
            raise ServiceUnavailableError(
                "Answer generation is not configured (missing LLM credentials)", code="llm_not_configured"
            )
        if model_id is not None and not models.is_selectable(model_id):
            # Unknown, no API key for its provider, or not on the CHAT_MODEL_IDS allowlist.
            raise ValidationFailedError(f"Unknown or unavailable model: {model_id}", code="unknown_model")
        chosen = model_id or default_id
        models.get(chosen)  # builds the provider; nothing is stored if that fails
        question = content.strip()
        async with self.sessionmaker() as s:
            conv = await self._owned(s, principal, conversation_id)
            live = (Message.conversation_id == conv.id, Message.superseded_at.is_(None))

            edited: Message | None = None
            if edit_message_id is not None:
                edited = await s.get(Message, edit_message_id)
                if (
                    edited is None
                    or edited.conversation_id != conv.id
                    or edited.role != "user"
                    or edited.superseded_at is not None
                ):
                    raise NotFoundError("Message not found", code="message_not_found")

            history_query = select(Message).where(*live, Message.status == "complete")
            if edited is not None:
                history_query = history_query.where(_before(edited))
            previous = (
                (
                    await s.execute(
                        history_query.order_by(Message.created_at.desc(), Message.id.desc()).limit(
                            self.settings.chat_history_turns * 2
                        )
                    )
                )
                .scalars()
                .all()
            )
            history = [ChatTurn(role=m.role, content=m.content) for m in reversed(previous)]  # type: ignore[arg-type]

            if edited is not None:
                await s.execute(update(Message).where(*live, _from(edited)).values(superseded_at=datetime.now(UTC)))

            user_msg = Message(
                conversation_id=conv.id,
                role="user",
                content=question,
                status="complete",
                edit_of=edited.id if edited is not None else None,
            )
            assistant_msg = Message(conversation_id=conv.id, role="assistant", content="", status="streaming")
            s.add_all([user_msg, assistant_msg])
            if conv.title == DEFAULT_TITLE or (edited is not None and conv.title == _title_for(edited.content)):
                conv.title = _title_for(question)  # the conversation was named after the message being replaced
            conv.updated_at = datetime.now(UTC)
            await s.commit()
            scope = scope_override if scope_override is not None else conv.scope
            return PreparedAsk(
                principal=principal,
                conversation_id=conv.id,
                user_message_id=user_msg.id,
                assistant_message_id=assistant_msg.id,
                question=question,
                history=history,
                filters=RetrievalFilters.from_scope(scope),
                model_id=chosen,
            )

    async def stream_answer(self, ask: PreparedAsk) -> AsyncIterator[ChatEvent]:
        models = self.rag.models
        llm = models.get(ask.model_id)
        router_id = models.router_id or ask.model_id
        yield ChatEvent(
            "meta",
            {
                "conversation_id": str(ask.conversation_id),
                "user_message_id": str(ask.user_message_id),
                "message_id": str(ask.assistant_message_id),
                "model": ask.model_id,
            },
        )
        tracker: CitationTracker | None = None
        status = "error"
        content = ""
        error: str | None = None
        search_query = ask.question
        route = Route.DOCUMENTS
        served_model: str | None = None
        answer_usage: dict[str, Any] = {}
        routing_usage: dict[str, Any] = {}
        try:
            yield ChatEvent("status", {"stage": "thinking"})
            decision = await route_message(
                models.get(router_id),
                ask.question,
                ask.history,
                scoped=bool(ask.filters.document_ids or ask.filters.category_ids),
                usage_out=routing_usage,
            )
            route = decision.route

            if route is Route.CHAT:
                yield ChatEvent("status", {"stage": "generating"})
                async for event in llm.stream_chat(question=ask.question, history=ask.history):
                    if isinstance(event, TextDelta):
                        content += event.text
                        yield ChatEvent("token", {"text": event.text})
                    elif isinstance(event, GenerationDone):
                        served_model, answer_usage = event.model, event.usage
                        if event.refused and not content:
                            content = REFUSAL_TEXT
                            yield ChatEvent("token", {"text": content})
                status = "complete"
            else:
                search_query = decision.query
                yield ChatEvent("status", {"stage": "retrieving"})
                result = await self.rag.retriever.retrieve(
                    ask.principal, search_query, filters=ask.filters, token_budget=self.settings.context_max_tokens
                )
                await log_retrieval(
                    self.sessionmaker, ask.principal, result, source="chat", message_id=ask.assistant_message_id
                )

                if not result.chunks:
                    content = NO_ANSWER_TEXT
                    yield ChatEvent("sources", {"sources": []})
                    yield ChatEvent("token", {"text": content})
                    status = "complete"
                else:
                    yield ChatEvent(
                        "sources", {"sources": [source_read(c).model_dump(mode="json") for c in result.chunks]}
                    )
                    yield ChatEvent("status", {"stage": "generating"})
                    sources = to_source_documents(result.chunks)
                    tracker = CitationTracker(result.chunks, sources)
                    async for event in llm.stream_answer(question=ask.question, sources=sources, history=ask.history):
                        for out in tracker.feed(event):
                            if isinstance(out, AnswerToken):
                                yield ChatEvent("token", {"text": out.text})
                            elif isinstance(out, NewCitation):
                                yield ChatEvent("citation", citation_read(out.citation).model_dump(mode="json"))
                    content = tracker.answer
                    if tracker.done is not None:
                        served_model, answer_usage = tracker.done.model, tracker.done.usage
                        if tracker.done.refused and not content:
                            content = REFUSAL_TEXT
                            yield ChatEvent("token", {"text": content})
                    status = "complete"
        except GenerationError as exc:
            error = exc.code
            if exc.code == "llm_quota_exceeded":
                models.mark_unavailable(ask.model_id, exc.message)
            content = tracker.answer if tracker else content
            yield ChatEvent("error", {"code": exc.code, "message": exc.message, "retryable": exc.retryable})
        except (asyncio.CancelledError, GeneratorExit):
            status, content = "cancelled", tracker.answer if tracker else content
            raise
        except Exception:
            log.exception("chat_answer_failed", message_id=str(ask.assistant_message_id))
            error, content = "internal_error", tracker.answer if tracker else content
            yield ChatEvent("error", {"code": "internal_error", "message": "Something went wrong while answering"})
        finally:
            latency_ms = int((time.perf_counter() - ask.started) * 1000)
            citations = tracker.ordered_citations if tracker else []
            if status == "complete":
                models.clear_unavailable(ask.model_id)
            usage = usage_record(
                models,
                answer_model=ask.model_id,
                answer_usage=answer_usage,
                served_model=served_model,
                router_model=router_id,
                routing_usage=routing_usage,
            )
            # Shield: persistence must complete even if the client disconnected (task cancelled).
            await asyncio.shield(
                self._finalize(ask, status, content, citations, search_query, route, usage, latency_ms, error)
            )
        yield ChatEvent(
            "done",
            {
                "message_id": str(ask.assistant_message_id),
                "grounded": bool(citations),
                "route": route.value,
                "model": ask.model_id,
                "citations": [citation_read(c).model_dump(mode="json") for c in citations],
                "citations_rejected": tracker.rejected if tracker else 0,
                "usage": usage,
                "latency_ms": latency_ms,
            },
        )

    async def _finalize(
        self,
        ask: PreparedAsk,
        status: str,
        content: str,
        citations: builtins.list[Citation],
        search_query: str,
        route: Route,
        usage: dict[str, Any],
        latency_ms: int,
        error: str | None,
    ) -> None:
        async with self.sessionmaker() as s:
            msg = await s.get(Message, ask.assistant_message_id)
            if msg is None:
                return
            msg.status = status
            msg.content = content.strip()
            msg.grounded = bool(citations)
            msg.search_query = search_query if search_query != ask.question else None
            msg.route = route.value
            msg.model = ask.model_id
            msg.usage = usage
            msg.latency_ms = latency_ms
            msg.error = error
            s.add_all(
                MessageCitation(
                    message_id=msg.id,
                    citation_index=c.number,
                    chunk_id=c.chunk.chunk_id,
                    document_id=c.chunk.document_id,
                    document_title=c.chunk.document_title[:300],
                    page_start=c.chunk.page_start,
                    page_end=c.chunk.page_end,
                    sheet_name=c.chunk.sheet_name,
                    row_start=c.chunk.row_start,
                    row_end=c.chunk.row_end,
                    section_path=(c.chunk.section_path or None) and c.chunk.section_path[:1000],
                    cited_text=c.snippet,
                    score=c.chunk.relevance,
                )
                for c in citations
            )
            await s.commit()
