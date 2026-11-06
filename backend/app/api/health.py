from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import Resources

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _check(awaitable: Awaitable[Any], limit_seconds: float = 2.0) -> str:
    try:
        await asyncio.wait_for(awaitable, limit_seconds)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


@router.get("/ready", summary="Readiness probe: PostgreSQL, Redis and Qdrant reachable")
async def ready(resources: Resources) -> JSONResponse:
    async def db_ping() -> None:
        async with resources.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    checks = {"postgres": await _check(db_ping())}
    checks["redis"] = await _check(resources.redis.ping()) if resources.redis is not None else "not configured"
    checks["qdrant"] = (
        await _check(resources.qdrant.get_collections()) if resources.qdrant is not None else "not configured"
    )
    healthy = all(v in ("ok", "not configured") for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503, content={"status": "ok" if healthy else "degraded", "checks": checks}
    )
