from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.helpers import pdf_bytes
from tests.integration.api_helpers import register, upload


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"max_upload_size_mb": 1, "max_files_per_upload": 3}


async def test_per_file_size_limit(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    resp = await upload(admin, [("big.pdf", pdf_bytes() + b"0" * (1024 * 1024)), ("ok.pdf", pdf_bytes())])
    assert resp.status_code == 202
    results = {r["filename"]: r for r in resp.json()["results"]}
    assert results["big.pdf"]["error"]["code"] == "file_too_large"
    assert results["ok.pdf"]["status"] == "accepted"


async def test_file_count_limit(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    resp = await upload(admin, [(f"{i}.pdf", pdf_bytes(str(i))) for i in range(4)])
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "too_many_files"


async def test_request_body_limit(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    # 3 files x 1 MB + overhead is the cap for the whole multipart body.
    huge = b"0" * (5 * 1024 * 1024)
    resp = await upload(admin, [("a.pdf", huge)])
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
