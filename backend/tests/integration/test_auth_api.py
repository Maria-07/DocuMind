from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.resources import AppResources
from app.models import AuditLog, RefreshToken
from tests.integration.api_helpers import PASSWORD, add_member, login, register

ClientFactory = Callable[[], httpx.AsyncClient]
COOKIE = "documind_refresh"


async def refresh_with(make_client: ClientFactory, token: str | None) -> httpx.Response:
    """Present a specific refresh token from a fresh client (no cookie-jar interference)."""
    return await make_client().post("/api/v1/auth/refresh", headers={"Cookie": f"{COOKIE}={token}"})


async def test_register_creates_org_admin_and_sets_refresh_cookie(client: httpx.AsyncClient) -> None:
    session = await register(client, email="Founder@Example.com", org="Acme Corp")
    assert session.user["email"] == "founder@example.com"  # normalized
    assert session.user["role"] == "admin"
    assert client.cookies.get(COOKIE)

    me = await client.get("/api/v1/auth/me", headers=session.headers)
    assert me.status_code == 200
    body = me.json()
    assert body["organization"]["slug"] == "acme-corp"
    assert "users:manage" in body["permissions"]


async def test_register_rejects_duplicate_email_and_weak_password(client: httpx.AsyncClient) -> None:
    await register(client, email="dup@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "DUP@example.com", "password": PASSWORD, "full_name": "X", "organization_name": "Other"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"

    weak = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "password", "full_name": "X", "organization_name": "Org"},
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "weak_password"


async def test_validation_errors_never_echo_input(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "hunter2-secret"})
    assert resp.status_code == 422
    assert "hunter2-secret" not in resp.text
    assert resp.json()["error"]["request_id"]


async def test_login_success_and_generic_failure(client: httpx.AsyncClient) -> None:
    await register(client, email="ada@example.com")
    assert (await login(client, "ada@example.com")).status_code == 200

    wrong = await login(client, "ada@example.com", "Wrong-password-1")
    unknown = await login(client, "nobody@example.com", "Wrong-password-1")
    assert wrong.status_code == unknown.status_code == 401
    # Same error for wrong password and unknown e-mail (no account enumeration via login).
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"login_max_failed_attempts": 3, "login_lockout_minutes": 5}


async def test_account_locks_after_repeated_failures(client: httpx.AsyncClient, resources: AppResources) -> None:
    await register(client, email="lock@example.com")
    for _ in range(3):
        assert (await login(client, "lock@example.com", "Wrong-password-1")).status_code == 401
    # The correct password is refused too until the lockout expires.
    assert (await login(client, "lock@example.com")).status_code == 401

    async with resources.sessionmaker() as s:
        rows = (await s.execute(select(AuditLog.action, AuditLog.details))).all()
    assert any(action == "auth.login_failed" and "locked_until" in details for action, details in rows)


async def test_refresh_rotates_and_detects_reuse(
    client: httpx.AsyncClient, make_client: ClientFactory, resources: AppResources
) -> None:
    await register(client, email="rot@example.com")
    first = client.cookies.get(COOKIE)

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    second = client.cookies.get(COOKIE)
    assert second and second != first
    new_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=new_headers)).status_code == 200

    # Replaying the rotated token outside the grace window revokes the whole family.
    resources.settings.refresh_reuse_grace_seconds = 0
    replay = await refresh_with(make_client, first)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh_token"
    assert (await refresh_with(make_client, second)).status_code == 401  # the successor is revoked too

    async with resources.sessionmaker() as s:
        tokens = (await s.execute(select(RefreshToken))).scalars().all()
        assert tokens and all(t.revoked_at is not None for t in tokens)
        assert await s.scalar(select(AuditLog.id).where(AuditLog.action == "auth.refresh_reuse_detected"))


async def test_concurrent_refresh_within_grace_keeps_session(
    client: httpx.AsyncClient, make_client: ClientFactory
) -> None:
    await register(client, email="race@example.com")
    first = client.cookies.get(COOKIE)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    stale = await refresh_with(make_client, first)
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "refresh_token_rotated"
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200  # current session survives


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    await register(client, email="out@example.com")
    token = client.cookies.get(COOKIE)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await refresh_with(make_client, token)).status_code == 401


async def test_missing_or_bad_access_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    bad = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401
    assert bad.headers["WWW-Authenticate"] == "Bearer"


async def test_deactivated_user_loses_access_immediately(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    member = await add_member(admin, make_client())
    assert (await member.client.get("/api/v1/auth/me", headers=member.headers)).status_code == 200

    resp = await client.delete(f"/api/v1/users/{member.user_id}", headers=admin.headers)
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    # The unexpired access token is refused: the principal is re-loaded from the DB on every request.
    assert (await member.client.get("/api/v1/auth/me", headers=member.headers)).status_code == 401
    assert (await member.client.post("/api/v1/auth/refresh")).status_code == 401


async def test_change_password_signs_out_everywhere(client: httpx.AsyncClient) -> None:
    session = await register(client, email="pw@example.com")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers=session.headers,
        json={"current_password": PASSWORD, "new_password": "An0ther-good-pass"},
    )
    assert resp.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    assert (await login(client, "pw@example.com")).status_code == 401
    assert (await login(client, "pw@example.com", "An0ther-good-pass")).status_code == 200
