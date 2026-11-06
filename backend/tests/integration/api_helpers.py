from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx

PASSWORD = "Sup3r-secret-pass"


@dataclass
class Session:
    client: httpx.AsyncClient
    token: str
    user: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def user_id(self) -> str:
        return str(self.user["id"])

    @property
    def org_id(self) -> str:
        return str(self.user["organization_id"])


async def register(client: httpx.AsyncClient, email: str | None = None, org: str = "Acme") -> Session:
    email = email or f"admin-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Ada Admin", "organization_name": org},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return Session(client, body["access_token"], body["user"])


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def add_member(admin: Session, other_client: httpx.AsyncClient, role: str = "user") -> Session:
    """Admin creates a user in their org; the user logs in on ``other_client``."""
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    resp = await admin.client.post(
        "/api/v1/users",
        headers=admin.headers,
        json={"email": email, "full_name": f"{role.title()} Person", "role": role, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    login_resp = await login(other_client, email)
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    return Session(other_client, body["access_token"], body["user"])


async def upload(session: Session, files: list[tuple[str, bytes]], **form: str) -> httpx.Response:
    return await session.client.post(
        "/api/v1/documents/upload",
        headers=session.headers,
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        data=form,
    )


async def upload_one(session: Session, name: str, data: bytes, **form: str) -> dict[str, Any]:
    resp = await upload(session, [(name, data)], **form)
    assert resp.status_code == 202, resp.text
    result = resp.json()["results"][0]
    assert result["status"] == "accepted", result
    return dict(result["document"])
