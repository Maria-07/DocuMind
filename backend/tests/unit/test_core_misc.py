from __future__ import annotations

import time
import uuid
from pathlib import Path

import fakeredis
import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.body_limit import BodySizeLimitMiddleware
from app.core.permissions import Permission, permissions_for
from app.core.rate_limit import RateLimiter
from app.models.enums import Role
from app.services.storage import LocalStorage
from app.utils.ids import uuid7
from app.utils.text import escape_like, sanitize_filename, slugify


def test_role_permissions() -> None:
    assert permissions_for(Role.ADMIN) == frozenset(Permission)
    assert Permission.DOCUMENTS_UPLOAD in permissions_for(Role.MANAGER)
    assert Permission.USERS_MANAGE not in permissions_for(Role.MANAGER)
    assert permissions_for(Role.USER) == frozenset()
    assert permissions_for(Role.USER, users_can_upload=True) == {Permission.DOCUMENTS_UPLOAD}


def test_uuid7_version_and_ordering() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first.version == 7 and first.variant == uuid.RFC_4122
    assert first < second  # time-ordered across milliseconds
    burst = [uuid7() for _ in range(10_000)]  # many per millisecond
    assert len(set(burst)) == len(burst)
    assert burst == sorted(burst)  # monotonic within the process, even inside one millisecond
    assert all(u.version == 7 for u in burst)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C:\\Users\\me\\Report Q3.pdf", "Report Q3.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("bad\x00name\x1f.docx", "badname.docx"),
        ("", "unnamed"),
        (None, "unnamed"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension() -> None:
    name = sanitize_filename("a" * 400 + ".xlsx")
    assert len(name) == 255 and name.endswith(".xlsx")


def test_slugify_and_escape_like() -> None:
    assert slugify("Acme Corp. (EMEA)") == "acme-corp-emea"
    assert slugify("!!!") == "org"
    assert escape_like("50%_off\\") == "50\\%\\_off\\\\"


async def test_rate_limiter_blocks_after_limit() -> None:
    limiter = RateLimiter(fakeredis.FakeAsyncRedis(), enabled=True)
    results = [await limiter.hit("auth:ip:1.2.3.4", limit=3) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].reset_in_seconds > 0
    assert (await limiter.hit("auth:ip:5.6.7.8", limit=3)).allowed  # independent keys


async def test_rate_limiter_disabled_or_without_redis_allows() -> None:
    assert (await RateLimiter(None).hit("k", limit=0)).allowed
    assert (await RateLimiter(fakeredis.FakeAsyncRedis(), enabled=False).hit("k", limit=1)).allowed


async def test_local_storage_roundtrip_and_traversal_guard(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    src = storage.tmp_dir / "upload.part"
    src.write_bytes(b"hello world")
    await storage.save_file(src, "org/doc/original.pdf")
    assert not src.exists()
    assert await storage.exists("org/doc/original.pdf")
    assert b"".join([c async for c in storage.iter_bytes("org/doc/original.pdf", chunk_size=4)]) == b"hello world"

    await storage.delete("org/doc/original.pdf")
    assert not await storage.exists("org/doc/original.pdf")
    assert not (storage.objects_dir / "org").exists()  # empty dirs cleaned up
    assert storage.objects_dir.exists()

    for bad in ("../secret", "org/../../x", "/abs/path", "a\\b", ""):
        with pytest.raises(ValueError):
            await storage.exists(bad)


async def _echo(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"size": len(body)})


def _limited_app() -> BodySizeLimitMiddleware:
    app = Starlette(
        routes=[Route("/api/v1/documents/upload", _echo, methods=["POST"]), Route("/other", _echo, methods=["POST"])]
    )
    return BodySizeLimitMiddleware(app, default_limit=10, upload_limit=100, upload_path_suffix="/documents/upload")


async def test_body_limit_by_content_length_and_streamed_bytes() -> None:
    transport = httpx.ASGITransport(app=_limited_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.post("/other", content=b"x" * 10)).status_code == 200
        too_big = await client.post("/other", content=b"x" * 11)
        assert too_big.status_code == 413
        assert too_big.json()["error"]["code"] == "payload_too_large"
        assert (await client.post("/api/v1/documents/upload", content=b"x" * 100)).status_code == 200
        assert (await client.post("/api/v1/documents/upload", content=b"x" * 101)).status_code == 413

        async def chunks():  # no Content-Length: enforced on received bytes
            for _ in range(5):
                yield b"x" * 5

        assert (await client.post("/other", content=chunks())).status_code == 413
