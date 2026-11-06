"""Process-wide resources (connection pools, clients), created once in the app lifespan."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.db.session import create_engine, create_sessionmaker
from app.rag.vectorstore import QdrantVectorStore
from app.services.job_queue import ArqJobQueue, JobQueue
from app.services.storage import LocalStorage

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class AppResources:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: LocalStorage
    job_queue: JobQueue
    rate_limiter: RateLimiter
    redis: Redis | None = None
    qdrant: AsyncQdrantClient | None = None
    vector_store: QdrantVectorStore | None = None
    owns_connections: bool = True

    async def aclose(self) -> None:
        if not self.owns_connections:
            return
        if self.qdrant is not None:
            await self.qdrant.close()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


async def build_resources(settings: Settings) -> AppResources:
    engine = create_engine(settings)
    # The arq pool is a redis.asyncio.Redis subclass: reuse it for rate limiting too.
    arq_pool: ArqRedis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        timeout=10,
    )
    log.info("resources_ready", environment=settings.environment)
    return AppResources(
        settings=settings,
        engine=engine,
        sessionmaker=create_sessionmaker(engine),
        storage=LocalStorage(settings.storage_local_path),
        job_queue=ArqJobQueue(arq_pool),
        rate_limiter=RateLimiter(arq_pool, enabled=settings.rate_limit_enabled),
        redis=arq_pool,
        qdrant=qdrant,
        vector_store=QdrantVectorStore(qdrant, settings.qdrant_collection_alias),
    )
