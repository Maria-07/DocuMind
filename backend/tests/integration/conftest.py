"""Integration fixtures: a real PostgreSQL schema built by the Alembic migrations.

Set TEST_DATABASE_URL to a *dedicated* database whose name contains "test", e.g.
``postgresql+asyncpg://documind:***@localhost:5432/documind_test``. The schema is dropped and
re-created on each run, so the guard below refuses any other database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.rate_limit import RateLimiter
from app.core.resources import AppResources
from app.db.base import Base
from app.db.session import create_sessionmaker
from app.main import create_app
from app.services.storage import LocalStorage
from tests.helpers import make_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not TEST_DATABASE_URL:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set"))


class RecordingQueue:
    """Captures enqueued jobs (the worker is exercised by its own tests)."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[str, ...], str | None]] = []

    async def enqueue(self, function: str, *args: str, job_id: str | None = None) -> bool:
        self.jobs.append((function, args, job_id))
        return True


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL or ""}
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, check=True, capture_output=True
    )


@pytest.fixture(scope="session")
def migrated_database() -> str:
    assert TEST_DATABASE_URL
    db_name = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in db_name:
        pytest.exit(f"Refusing to run destructive tests against database '{db_name}' (name must contain 'test')")
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def rate_limiter() -> RateLimiter:
    return RateLimiter(None, enabled=False)


@pytest.fixture
def resources(
    engine: AsyncEngine,
    tmp_path: Path,
    queue: RecordingQueue,
    rate_limiter: RateLimiter,
    settings_overrides: dict[str, Any],
) -> AppResources:
    settings = make_settings(
        database_url=TEST_DATABASE_URL, storage_local_path=tmp_path / "storage", **settings_overrides
    )
    return AppResources(
        settings=settings,
        engine=engine,
        sessionmaker=create_sessionmaker(engine),
        storage=LocalStorage(settings.storage_local_path),
        job_queue=queue,
        rate_limiter=rate_limiter,
        owns_connections=False,
    )


@pytest.fixture
async def client(resources: AppResources) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(resources.settings, resources)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def make_client(resources: AppResources):  # type: ignore[no-untyped-def]
    """Factory for extra clients with their own cookie jars (other users / tenants)."""
    app = create_app(resources.settings, resources)
    opened: list[httpx.AsyncClient] = []

    def _make() -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        opened.append(c)
        return c

    yield _make
    # Clients are closed by the event loop teardown; ASGITransport holds no sockets.
