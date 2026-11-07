"""Organization analytics: aggregates over documents, ingestion, retrieval logs and chat messages."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, Float, String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import Principal
from app.models import AuditLog, Conversation, Document, DocumentChunk, Message, RetrievalLog
from app.models.enums import DocumentStatus


def _days(days: int) -> list[date]:
    today = datetime.now(UTC).date()
    return [today - timedelta(days=i) for i in range(days - 1, -1, -1)]


def _series(rows: list[tuple[date, Any]], days: list[date]) -> list[float]:
    values = {d: v for d, v in rows}
    return [float(values.get(d) or 0) for d in days]


class AnalyticsService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def overview(self, principal: Principal) -> dict[str, Any]:
        org = principal.organization_id
        week_ago = datetime.now(UTC) - timedelta(days=7)
        async with self.sessionmaker() as s:
            live = and_(Document.organization_id == org, Document.deleted_at.is_(None))
            by_status = dict(
                (await s.execute(select(Document.status, func.count()).where(live).group_by(Document.status))).all()
            )
            storage = await s.scalar(
                select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
                    Document.organization_id == org, Document.purged_at.is_(None)
                )
            )
            chunks = await s.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .join(
                    Document,
                    and_(
                        Document.id == DocumentChunk.document_id,
                        Document.active_index_version == DocumentChunk.index_version,
                    ),
                )
                .where(live)
            )
            conversations = await s.scalar(
                select(func.count()).where(Conversation.organization_id == org, Conversation.deleted_at.is_(None))
            )
            questions_7d = await s.scalar(
                select(func.count()).where(
                    RetrievalLog.organization_id == org,
                    RetrievalLog.source == "chat",
                    RetrievalLog.created_at >= week_ago,
                )
            )
            answered = (
                await s.execute(
                    select(func.count(), func.count().filter(Message.grounded.is_(True)))
                    .select_from(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.organization_id == org,
                        Message.role == "assistant",
                        Message.status == "complete",
                        Message.created_at >= week_ago,
                    )
                )
            ).one()
        return {
            "documents_total": sum(by_status.values()),
            "documents_by_status": {st.value: by_status.get(st, 0) for st in DocumentStatus},
            "chunks_total": int(chunks or 0),
            "storage_bytes": int(storage or 0),
            "conversations_total": int(conversations or 0),
            "questions_7d": int(questions_7d or 0),
            "grounded_rate_7d": (answered[1] / answered[0]) if answered[0] else None,
        }

    async def documents(self, principal: Principal, days: int) -> dict[str, Any]:
        org = principal.organization_id
        span = _days(days)
        since = datetime.combine(span[0], datetime.min.time(), tzinfo=UTC)
        async with self.sessionmaker() as s:
            day = cast(Document.created_at, Date)
            uploads = (
                await s.execute(
                    select(day, func.count())
                    .where(Document.organization_id == org, Document.created_at >= since)
                    .group_by(day)
                )
            ).all()
            iday = cast(Document.indexed_at, Date)
            indexed = (
                await s.execute(
                    select(iday, func.count())
                    .where(Document.organization_id == org, Document.indexed_at >= since)
                    .group_by(iday)
                )
            ).all()
            fday = cast(Document.updated_at, Date)
            failed = (
                await s.execute(
                    select(fday, func.count())
                    .where(
                        Document.organization_id == org,
                        Document.status == DocumentStatus.FAILED,
                        Document.updated_at >= since,
                        Document.deleted_at.is_(None),
                    )
                    .group_by(fday)
                )
            ).all()
            by_type = dict(
                (
                    await s.execute(
                        select(Document.file_type, func.count())
                        .where(Document.organization_id == org, Document.deleted_at.is_(None))
                        .group_by(Document.file_type)
                    )
                ).all()
            )
            activity = (
                await s.execute(
                    select(AuditLog.action, AuditLog.created_at, Document.title, AuditLog.resource_id)
                    # Current title (documents are soft-deleted, so deleted ones keep theirs).
                    .outerjoin(
                        Document,
                        and_(Document.organization_id == org, cast(Document.id, String) == AuditLog.resource_id),
                    )
                    .where(AuditLog.organization_id == org, AuditLog.action.like("document.%"))
                    .order_by(AuditLog.created_at.desc())
                    .limit(15)
                )
            ).all()
        return {
            "days": [d.isoformat() for d in span],
            "uploads": _series(uploads, span),
            "indexed": _series(indexed, span),
            "failed": _series(failed, span),
            "by_type": {t.value: n for t, n in by_type.items()},
            "recent_activity": [
                {"action": a, "at": at.isoformat(), "document_id": rid, "title": title}
                for a, at, title, rid in activity
            ],
        }

    async def retrieval(self, principal: Principal, days: int) -> dict[str, Any]:
        org = principal.organization_id
        span = _days(days)
        since = datetime.combine(span[0], datetime.min.time(), tzinfo=UTC)
        base = and_(RetrievalLog.organization_id == org, RetrievalLog.created_at >= since)
        day = cast(RetrievalLog.created_at, Date)

        def ms(key: str) -> Any:
            return cast(RetrievalLog.latency[key].astext, Float)

        total_ms = ms("embed_ms") + ms("search_ms") + ms("postcheck_ms") + func.coalesce(ms("rerank_ms"), 0.0)
        async with self.sessionmaker() as s:
            per_day = (
                await s.execute(
                    select(
                        day,
                        func.count(),
                        func.count().filter(RetrievalLog.no_answer.is_(True)),
                        func.avg(RetrievalLog.top_score),
                    )
                    .where(base)
                    .group_by(day)
                )
            ).all()
            pct = (
                await s.execute(
                    select(
                        func.percentile_cont(0.5).within_group(total_ms),
                        func.percentile_cont(0.95).within_group(total_ms),
                        func.avg(ms("embed_ms")),
                        func.avg(ms("search_ms")),
                        func.avg(ms("postcheck_ms")),
                        func.avg(ms("rerank_ms")),
                        func.count(),
                        func.count().filter(RetrievalLog.no_answer.is_(True)),
                        func.avg(RetrievalLog.filtered_out),
                    ).where(base)
                )
            ).one()
            gen = (
                await s.execute(
                    select(
                        func.percentile_cont(0.5).within_group(Message.latency_ms),
                        func.percentile_cont(0.95).within_group(Message.latency_ms),
                        func.count(),
                        func.count().filter(Message.grounded.is_(True)),
                        func.sum(cast(Message.usage["input_tokens"].astext, Float)),
                        func.sum(cast(Message.usage["output_tokens"].astext, Float)),
                    )
                    .select_from(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.organization_id == org,
                        Message.role == "assistant",
                        Message.status == "complete",
                        Message.created_at >= since,
                    )
                )
            ).one()
        counts = {d: (n, na, sc) for d, n, na, sc in per_day}
        return {
            "days": [d.isoformat() for d in span],
            "queries": [float(counts.get(d, (0, 0, 0))[0]) for d in span],
            "no_answer": [float(counts.get(d, (0, 0, 0))[1]) for d in span],
            "avg_top_relevance": [counts[d][2] if d in counts and counts[d][2] is not None else None for d in span],
            "retrieval_latency_ms": {
                "p50": pct[0],
                "p95": pct[1],
                "embed": pct[2],
                "search": pct[3],
                "postcheck": pct[4],
                "rerank": pct[5],
            },
            "queries_total": int(pct[6] or 0),
            "no_answer_rate": (pct[7] / pct[6]) if pct[6] else None,
            "avg_filtered_by_postcheck": float(pct[8]) if pct[8] is not None else None,
            "answer_latency_ms": {"p50": gen[0], "p95": gen[1]},
            "answers_total": int(gen[2] or 0),
            "grounded_rate": (gen[3] / gen[2]) if gen[2] else None,
            "tokens": {"input": int(gen[4] or 0), "output": int(gen[5] or 0)},
        }
