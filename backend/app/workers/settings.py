"""arq worker entrypoint: ``arq app.workers.settings.WorkerSettings``."""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_sessionmaker
from app.services.storage import LocalStorage
from app.workers.tasks import purge_document

_settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level, _settings.log_json)
    engine = create_engine(_settings)
    ctx["settings"] = _settings
    ctx["engine"] = engine
    ctx["sessionmaker"] = create_sessionmaker(engine)
    ctx["storage"] = LocalStorage(_settings.storage_local_path)
    ctx["qdrant"] = AsyncQdrantClient(
        url=_settings.qdrant_url,
        api_key=_settings.qdrant_api_key.get_secret_value() if _settings.qdrant_api_key else None,
        timeout=30,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["qdrant"].close()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [purge_document]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_tries = 3
    job_timeout = 15 * 60
    keep_result = 3600
