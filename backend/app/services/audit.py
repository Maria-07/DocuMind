from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


@dataclass(frozen=True, slots=True)
class RequestMeta:
    ip: str | None
    user_agent: str | None


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    meta: RequestMeta | None,
    organization_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the current unit of work (committed with the action it describes)."""
    session.add(
        AuditLog(
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip=meta.ip if meta else None,
            user_agent=(meta.user_agent or "")[:512] or None if meta else None,
            details=details or {},
        )
    )
