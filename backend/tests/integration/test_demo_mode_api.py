"""Public-demo safeguards: closing sign-up, the public config endpoint, and the create-admin command."""

from __future__ import annotations

import httpx
import pytest

from app.cli import create_admin
from app.core.resources import AppResources
from tests.integration.api_helpers import PASSWORD, login, register


async def test_config_endpoint_is_public_and_reflects_the_setting(
    client: httpx.AsyncClient, resources: AppResources
) -> None:
    assert (await client.get("/api/v1/auth/config")).json() == {"registration_enabled": True}  # no token needed
    resources.settings.registration_enabled = False
    assert (await client.get("/api/v1/auth/config")).json() == {"registration_enabled": False}


async def test_closed_sign_up_is_refused_but_existing_accounts_still_work(
    client: httpx.AsyncClient, resources: AppResources
) -> None:
    existing = await register(client, email="owner@example.com")
    resources.settings.registration_enabled = False

    refused = await client.post(
        "/api/v1/auth/register",
        json={"email": "stranger@example.com", "password": PASSWORD, "full_name": "S", "organization_name": "Other"},
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "registration_disabled"
    assert (await login(client, "stranger@example.com")).status_code == 401  # nothing was created

    assert (await login(client, "owner@example.com")).status_code == 200  # current users are unaffected
    me = await client.get("/api/v1/auth/me", headers=existing.headers)
    assert me.status_code == 200


async def test_create_admin_works_while_sign_up_is_closed(
    client: httpx.AsyncClient, resources: AppResources, capsys: pytest.CaptureFixture[str]
) -> None:
    resources.settings.registration_enabled = False
    args = {"name": "Demo Admin", "organization": "Demo Org", "password": "Demo-pass-12345"}

    assert await create_admin(resources.sessionmaker, resources.settings, email="Demo@Example.com", **args) == 0
    assert "Created organization 'Demo Org' with admin demo@example.com" in capsys.readouterr().out

    signed_in = await login(client, "demo@example.com", "Demo-pass-12345")
    assert signed_in.status_code == 200
    body = signed_in.json()
    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})).json()
    assert me["user"]["role"] == "admin" and me["user"]["email"] == "demo@example.com"

    # The same address again, a weak password and a malformed address are refused with a clear message.
    assert await create_admin(resources.sessionmaker, resources.settings, email="demo@example.com", **args) == 2
    assert "already exists" in capsys.readouterr().err
    weak = {**args, "password": "OnlyLettersHere"}  # long enough, but no digit
    assert await create_admin(resources.sessionmaker, resources.settings, email="new@example.com", **weak) == 2
    assert "Password does not meet requirements: must contain a digit" in capsys.readouterr().err
    assert await create_admin(resources.sessionmaker, resources.settings, email="not-an-email", **args) == 2
    assert "Invalid input: email" in capsys.readouterr().err
