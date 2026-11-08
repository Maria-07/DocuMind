"""Conversations and grounded, streamed answers with citations.

Streaming flow for one message (events in brackets are sent to the client):

    [meta] → retrieve (condensed follow-up query) → [sources] → LLM stream → [token]* / [citation]*
           → persist answer + citations + retrieval log → [done]          (or [error] at any point)

If retrieval finds nothing relevant enough, the model is not called at all and the answer says so.
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import NotFoundError, ServiceUnavailableError
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
from app.rag.generation.base import ChatTurn, GenerationError
from app.rag.retrieval import RetrievalFilters, RetrievedChunk
from app.rag.runtime import RagRuntime
from app.schemas.chat import CitationRead, ConversationCreate, ConversationUpdate, MessageRead, SourceRead
from app.schemas.common import PageParams
from app.services.search import log_retrieval

log = structlog.get_logger(__name__)

DEFAULT_TITLE = "New conversation"


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
                        .where(Message.conversation_id == conv.id)
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
    async def prepare_ask(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        content: str,
        scope_override: dict[str, Any] | None = None,
    ) -> PreparedAsk:
        """Validate and persist the user message *before* streaming starts (so errors are proper HTTP)."""
        if self.rag.llm is None:
            raise ServiceUnavailableError(
                "Answer generation is not configured (missing LLM credentials)", code="llm_not_configured"
            )
        question = content.strip()
        async with self.sessionmaker() as s:
            conv = await self._owned(s, principal, conversation_id)
            previous = (
                (
                    await s.execute(
                        select(Message)
                        .where(Message.conversation_id == conv.id, Message.status == "complete")
                        .order_by(Message.created_at.desc(), Message.id.desc())
                        .limit(self.settings.chat_history_turns * 2)
                    )
                )
                .scalars()
                .all()
            )
            history = [ChatTurn(role=m.role, content=m.content) for m in reversed(previous)]  # type: ignore[arg-type]

            user_msg = Message(conversation_id=conv.id, role="user", content=question, status="complete")
            assistant_msg = Message(conversation_id=conv.id, role="assistant", content="", status="streaming")
            s.add_all([user_msg, assistant_msg])
            if conv.title == DEFAULT_TITLE:
                conv.title = question[:80] + ("…" if len(question) > 80 else "")
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
            )

    async def stream_answer(self, ask: PreparedAsk) -> AsyncIterator[ChatEvent]:
        llm = self.rag.llm
        assert llm is not None
        yield ChatEvent(
            "meta",
            {
                "conversation_id": str(ask.conversation_id),
                "user_message_id": str(ask.user_message_id),
                "message_id": str(ask.assistant_message_id),
            },
        )
        tracker: CitationTracker | None = None
        status = "error"
        content = ""
        error: str | None = None
        search_query = ask.question
        model: str | None = None
        usage: dict[str, Any] = {}
        try:
            yield ChatEvent("status", {"stage": "retrieving"})
            if ask.history:
                search_query = await llm.condense_question(question=ask.question, history=ask.history)
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
                yield ChatEvent("sources", {"sources": [source_read(c).model_dump(mode="json") for c in result.chunks]})
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
                    model, usage = tracker.done.model, tracker.done.usage
                    if tracker.done.refused and not content:
                        content = REFUSAL_TEXT
                        yield ChatEvent("token", {"text": content})
                status = "complete"
        except GenerationError as exc:
            error = exc.code
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
            # Shield: persistence must complete even if the client disconnected (task cancelled).
            await asyncio.shield(
                self._finalize(ask, status, content, citations, search_query, model, usage, latency_ms, error)
            )
        yield ChatEvent(
            "done",
            {
                "message_id": str(ask.assistant_message_id),
                "grounded": bool(citations),
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
        model: str | None,
        usage: dict[str, Any],
        latency_ms: int,
        error: str | None,
    ) -> None:
        async with self.sessionmaker() as s:
            msg = await s.get(Message, ask.assistant_message_id)
            if msg is None:
                return
            msg.status = status
            msg.content = content
            msg.grounded = bool(citations)
            msg.search_query = search_query if search_query != ask.question else None
            msg.model = model
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
