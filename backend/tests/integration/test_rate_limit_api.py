from __future__ import annotations

import fakeredis
import httpx
import pytest

from app.core.rate_limit import RateLimiter
from app.core.resources import AppResources
from tests.integration.api_helpers import login


@pytest.fixture
def rate_limiter() -> RateLimiter:
    return RateLimiter(fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()), enabled=True)


async def test_login_is_rate_limited(client: httpx.AsyncClient, resources: AppResources) -> None:
    resources.settings.rate_limit_auth_per_minute = 3
    codes = [(await login(client, "x@example.com", "Wrong-password-1")).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    limited = await login(client, "x@example.com", "Wrong-password-1")
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
