from __future__ import annotations

import fakeredis
import httpx
import pytest

from app.core.rate_limit import RateLimiter
from app.core.resources import AppResources
from tests.integration.api_helpers import login, register


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


async def test_refresh_has_its_own_budget(client: httpx.AsyncClient, resources: AppResources) -> None:
    """Regression: refresh shared the login budget, so normal page loads signed users out (429)."""
    resources.settings.rate_limit_auth_per_minute = 2
    resources.settings.rate_limit_refresh_per_minute = 5
    await register(client, email="budget@example.com")  # 1 of 2 auth requests
    assert (await login(client, "budget@example.com")).status_code == 200  # 2 of 2
    assert (await login(client, "budget@example.com")).status_code == 429  # auth budget spent
    refresh_codes = [(await client.post("/api/v1/auth/refresh")).status_code for _ in range(6)]
    assert refresh_codes == [200] * 5 + [429]  # independent, larger budget
