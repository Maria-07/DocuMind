from __future__ import annotations

from collections.abc import Callable

import httpx

from tests.integration.api_helpers import add_member, register

ClientFactory = Callable[[], httpx.AsyncClient]


async def test_user_management_requires_admin(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    manager = await add_member(admin, make_client(), role="manager")
    for method, path in [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("PATCH", f"/api/v1/users/{admin.user_id}"),
    ]:
        resp = await manager.client.request(method, path, headers=manager.headers, json={})
        assert resp.status_code in (403, 422), (method, path)
        if resp.status_code == 422:  # body validation may run first; it must still never succeed
            continue
        assert resp.json()["error"]["code"] == "permission_denied"


async def test_role_change_takes_effect_immediately(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    member = await add_member(admin, make_client(), role="user")
    me = (await member.client.get("/api/v1/auth/me", headers=member.headers)).json()
    assert "documents:upload" not in me["permissions"]

    resp = await client.patch(f"/api/v1/users/{member.user_id}", headers=admin.headers, json={"role": "manager"})
    assert resp.status_code == 200
    me = (await member.client.get("/api/v1/auth/me", headers=member.headers)).json()
    assert "documents:upload" in me["permissions"]  # same access token, new permissions


async def test_cannot_remove_last_admin(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    for body in ({"role": "user"}, {"is_active": False}):
        resp = await client.patch(f"/api/v1/users/{admin.user_id}", headers=admin.headers, json=body)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "last_admin"


async def test_users_are_scoped_to_organization(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    acme = await register(client, org="Acme")
    globex = await register(make_client(), org="Globex")
    assert (await client.get(f"/api/v1/users/{globex.user_id}", headers=acme.headers)).status_code == 404
    listing = (await client.get("/api/v1/users", headers=acme.headers)).json()
    assert [u["id"] for u in listing["items"]] == [acme.user_id]


async def test_categories_crud_and_permissions(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    user = await add_member(admin, make_client())
    created = await client.post("/api/v1/categories", headers=admin.headers, json={"name": "Legal", "color": "#1D4ED8"})
    assert created.status_code == 201
    dup = await client.post("/api/v1/categories", headers=admin.headers, json={"name": "legal"})
    assert dup.status_code == 409
    assert (await user.client.post("/api/v1/categories", headers=user.headers, json={"name": "X"})).status_code == 403
    assert len((await user.client.get("/api/v1/categories", headers=user.headers)).json()) == 1
    cid = created.json()["id"]
    assert (await client.delete(f"/api/v1/categories/{cid}", headers=admin.headers)).status_code == 204


async def test_group_members_must_belong_to_org(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    outsider = await register(make_client(), org="Other")
    group = (await client.post("/api/v1/groups", headers=admin.headers, json={"name": "Ops"})).json()
    resp = await client.put(
        f"/api/v1/groups/{group['id']}/members", headers=admin.headers, json={"user_ids": [outsider.user_id]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_users"


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["postgres"] == "ok"
    assert ready.headers["x-content-type-options"] == "nosniff"
    assert ready.headers["x-request-id"]
