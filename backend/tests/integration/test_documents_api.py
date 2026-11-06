from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select

from app.core.resources import AppResources
from app.models import AuditLog, Document, IngestionJob
from app.services.job_queue import INGEST_DOCUMENT, PURGE_DOCUMENT
from tests.helpers import docx_bytes, pdf_bytes, xls_bytes, xlsx_bytes
from tests.integration.api_helpers import Session, add_member, register, upload, upload_one
from tests.integration.conftest import RecordingQueue

ClientFactory = Callable[[], httpx.AsyncClient]


# --- upload --------------------------------------------------------------------------------


async def test_upload_all_supported_types(
    client: httpx.AsyncClient, queue: RecordingQueue, resources: AppResources
) -> None:
    admin = await register(client)
    resp = await upload(
        admin,
        [
            ("Report.pdf", pdf_bytes("a")),
            ("Policy.docx", docx_bytes("b")),
            ("Budget.xlsx", xlsx_bytes("c")),
            ("Legacy.xls", xls_bytes("d")),
        ],
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert (body["accepted"], body["duplicates"], body["rejected"]) == (4, 0, 0)

    docs = [r["document"] for r in body["results"]]
    assert [d["file_type"] for d in docs] == ["pdf", "docx", "xlsx", "xls"]
    assert all(d["status"] == "uploaded" and d["visibility"] == "organization" for d in docs)
    assert docs[0]["title"] == "Report"

    # One ingestion job per document, queued by id only.
    assert [j[0] for j in queue.jobs] == [INGEST_DOCUMENT] * 4
    async with resources.sessionmaker() as s:
        stored = (await s.execute(select(Document))).scalars().all()
        jobs = (await s.execute(select(IngestionJob))).scalars().all()
    assert len(stored) == len(jobs) == 4
    for d in stored:
        path = resources.storage.local_path(d.storage_key)
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == d.sha256
    assert not any(resources.storage.tmp_dir.iterdir())  # temp files cleaned up


async def test_upload_rejects_bad_files_per_file(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    admin = await register(client)
    resp = await upload(
        admin,
        [
            ("ok.pdf", pdf_bytes()),
            ("evil.exe", b"MZ..."),
            ("fake.pdf", b"MZ not a pdf"),
            ("macro.docx", docx_bytes(extra={"word/vbaProject.bin": b"x"})),
            ("empty.xlsx", b""),
        ],
    )
    assert resp.status_code == 202
    statuses = {r["filename"]: (r["status"], (r["error"] or {}).get("code")) for r in resp.json()["results"]}
    assert statuses == {
        "ok.pdf": ("accepted", None),
        "evil.exe": ("rejected", "unsupported_file_type"),
        "fake.pdf": ("rejected", "content_mismatch"),
        "macro.docx": ("rejected", "macros_not_allowed"),
        "empty.xlsx": ("rejected", "empty_file"),
    }
    assert len(queue.jobs) == 1


async def test_duplicate_detection_and_reupload_after_delete(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    first = await upload_one(admin, "a.pdf", pdf_bytes("same"))

    dup = await upload(admin, [("renamed.pdf", pdf_bytes("same"))])
    assert dup.status_code == 200
    result = dup.json()["results"][0]
    assert result["status"] == "duplicate"
    assert result["document"]["id"] == first["id"]

    assert (await client.delete(f"/api/v1/documents/{first['id']}", headers=admin.headers)).status_code == 202
    again = await upload_one(admin, "a.pdf", pdf_bytes("same"))
    assert again["id"] != first["id"]


async def test_duplicate_of_invisible_document_does_not_leak_it(
    client: httpx.AsyncClient, make_client: ClientFactory
) -> None:
    admin = await register(client)
    await upload_one(admin, "secret.pdf", pdf_bytes("secret"), visibility="private")
    manager = await add_member(admin, make_client(), role="manager")

    result = (await upload(manager, [("mine.pdf", pdf_bytes("secret"))])).json()["results"][0]
    assert result["status"] == "duplicate"
    assert result["document"] is None
    assert "secret" not in result["error"]["message"]


async def test_upload_permission_by_role_and_org_setting(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    user = await add_member(admin, make_client(), role="user")
    manager = await add_member(admin, make_client(), role="manager")

    assert (await upload(user, [("u.pdf", pdf_bytes("u"))])).status_code == 403
    assert (await upload(manager, [("m.pdf", pdf_bytes("m"))])).status_code == 202

    resp = await client.patch(
        "/api/v1/organization", headers=admin.headers, json={"settings": {"users_can_upload": True}}
    )
    assert resp.status_code == 200 and resp.json()["settings"]["users_can_upload"] is True
    assert (await upload(user, [("u.pdf", pdf_bytes("u"))])).status_code == 202


async def test_upload_with_unknown_category_is_rejected(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    resp = await upload(admin, [("a.pdf", pdf_bytes())], category_id="00000000-0000-7000-8000-000000000000")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_category"


# --- tenant isolation & document permissions ------------------------------------------------


async def test_cross_tenant_isolation(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    acme = await register(client, org="Acme")
    globex = await register(make_client(), org="Globex")
    doc = await upload_one(acme, "acme-plan.pdf", pdf_bytes("acme"))
    doc_id = doc["id"]

    listing = await globex.client.get("/api/v1/documents", headers=globex.headers)
    assert listing.json()["total"] == 0
    # Every per-document endpoint answers 404 (not 403) to another tenant — even for an admin.
    for method, path in [
        ("GET", f"/api/v1/documents/{doc_id}"),
        ("GET", f"/api/v1/documents/{doc_id}/download"),
        ("GET", f"/api/v1/documents/{doc_id}/preview"),
        ("GET", f"/api/v1/documents/{doc_id}/jobs"),
        ("GET", f"/api/v1/documents/{doc_id}/permissions"),
        ("PATCH", f"/api/v1/documents/{doc_id}"),
        ("DELETE", f"/api/v1/documents/{doc_id}"),
        ("POST", f"/api/v1/documents/{doc_id}/reprocess"),
    ]:
        kwargs: dict[str, Any] = {"json": {"title": "pwned"}} if method == "PATCH" else {}
        resp = await globex.client.request(method, path, headers=globex.headers, **kwargs)
        assert resp.status_code == 404, (method, path, resp.status_code)

    # Same content in another org is not a duplicate: hashes are per organization.
    assert (await upload(globex, [("copy.pdf", pdf_bytes("acme"))])).json()["accepted"] == 1


async def test_visibility_and_grants(client: httpx.AsyncClient, make_client: ClientFactory) -> None:
    admin = await register(client)
    alice = await add_member(admin, make_client())
    bob = await add_member(admin, make_client())

    org_doc = await upload_one(admin, "handbook.pdf", pdf_bytes("1"), visibility="organization")
    private_doc = await upload_one(admin, "salaries.xlsx", xlsx_bytes("2"), visibility="private")
    restricted = await upload_one(admin, "m-and-a.docx", docx_bytes("3"), visibility="restricted")

    async def visible(s: Session) -> set[str]:
        return {d["id"] for d in (await s.client.get("/api/v1/documents", headers=s.headers)).json()["items"]}

    assert await visible(alice) == {org_doc["id"]}
    assert (await alice.client.get(f"/api/v1/documents/{private_doc['id']}", headers=alice.headers)).status_code == 404
    assert (await alice.client.get(f"/api/v1/documents/{restricted['id']}", headers=alice.headers)).status_code == 404
    assert await visible(admin) == {org_doc["id"], private_doc["id"], restricted["id"]}

    # Grant Alice view access via a group, Bob manage access directly.
    group = (await client.post("/api/v1/groups", headers=admin.headers, json={"name": "Deal team"})).json()
    put = await client.put(
        f"/api/v1/groups/{group['id']}/members", headers=admin.headers, json={"user_ids": [alice.user_id]}
    )
    assert put.status_code == 200
    grants = await client.put(
        f"/api/v1/documents/{restricted['id']}/permissions",
        headers=admin.headers,
        json={
            "grants": [
                {"principal_type": "group", "principal_id": group["id"], "permission": "view"},
                {"principal_type": "user", "principal_id": bob.user_id, "permission": "manage"},
            ]
        },
    )
    assert grants.status_code == 200 and len(grants.json()["grants"]) == 2

    assert await visible(alice) == {org_doc["id"], restricted["id"]}
    detail = (await alice.client.get(f"/api/v1/documents/{restricted['id']}", headers=alice.headers)).json()
    assert detail["can_manage"] is False
    patch = await alice.client.patch(
        f"/api/v1/documents/{restricted['id']}", headers=alice.headers, json={"title": "x"}
    )
    assert patch.status_code == 403
    assert (
        await bob.client.patch(
            f"/api/v1/documents/{restricted['id']}", headers=bob.headers, json={"title": "Project Falcon"}
        )
    ).json()["title"] == "Project Falcon"

    # Making it private removes everyone's grant-based access at once.
    await client.patch(f"/api/v1/documents/{restricted['id']}", headers=admin.headers, json={"visibility": "private"})
    assert restricted["id"] not in await visible(alice)
    assert restricted["id"] not in await visible(bob)


async def test_grants_must_reference_principals_in_same_org(
    client: httpx.AsyncClient, make_client: ClientFactory
) -> None:
    admin = await register(client)
    outsider = await register(make_client(), org="Other")
    doc = await upload_one(admin, "a.pdf", pdf_bytes(), visibility="restricted")
    resp = await client.put(
        f"/api/v1/documents/{doc['id']}/permissions",
        headers=admin.headers,
        json={"grants": [{"principal_type": "user", "principal_id": outsider.user_id}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_principals"


# --- lifecycle -----------------------------------------------------------------------------


async def test_download_preview_and_audit(client: httpx.AsyncClient, resources: AppResources) -> None:
    admin = await register(client)
    data = pdf_bytes("download-me")
    doc = await upload_one(admin, "Résumé 2026.pdf", data)

    dl = await client.get(f"/api/v1/documents/{doc['id']}/download", headers=admin.headers)
    assert dl.status_code == 200
    assert dl.content == data
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%202026.pdf" in dl.headers["content-disposition"]
    assert dl.headers["cache-control"] == "private, no-store"

    preview = await client.get(f"/api/v1/documents/{doc['id']}/preview", headers=admin.headers)
    assert preview.status_code == 200 and preview.headers["content-disposition"].startswith("inline;")

    xlsx = await upload_one(admin, "s.xlsx", xlsx_bytes())
    no_preview = await client.get(f"/api/v1/documents/{xlsx['id']}/preview", headers=admin.headers)
    assert no_preview.status_code == 409

    async with resources.sessionmaker() as s:
        actions = set((await s.execute(select(AuditLog.action))).scalars().all())
    assert {"document.uploaded", "document.downloaded", "document.previewed"} <= actions


async def test_delete_hides_immediately_and_enqueues_purge(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "a.pdf", pdf_bytes())
    assert (await client.delete(f"/api/v1/documents/{doc['id']}", headers=admin.headers)).status_code == 202
    assert (await client.get(f"/api/v1/documents/{doc['id']}", headers=admin.headers)).status_code == 404
    assert (await client.get("/api/v1/documents", headers=admin.headers)).json()["total"] == 0
    assert (PURGE_DOCUMENT, (doc["id"],), f"purge:{doc['id']}") in queue.jobs


async def test_reprocess_conflicts_while_queued(client: httpx.AsyncClient, resources: AppResources) -> None:
    admin = await register(client)
    doc = await upload_one(admin, "a.pdf", pdf_bytes())
    resp = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert resp.status_code == 409  # the upload's job is still queued
    assert resp.json()["error"]["code"] == "reprocess_in_progress"

    # Simulate the worker having failed the job; reprocess is then allowed.
    async with resources.sessionmaker() as s:
        job = (await s.execute(select(IngestionJob))).scalar_one()
        job.status = "failed"  # type: ignore[assignment]
        await s.commit()
    ok = await client.post(f"/api/v1/documents/{doc['id']}/reprocess", headers=admin.headers)
    assert ok.status_code == 202 and ok.json()["status"] == "queued"
    jobs = (await client.get(f"/api/v1/documents/{doc['id']}/jobs", headers=admin.headers)).json()
    assert len(jobs) == 2


async def test_list_filters_search_and_pagination(client: httpx.AsyncClient) -> None:
    admin = await register(client)
    category = (await client.post("/api/v1/categories", headers=admin.headers, json={"name": "Finance"})).json()
    await upload_one(admin, "Q1 report.pdf", pdf_bytes("1"), category_id=category["id"])
    await upload_one(admin, "Q2 report.pdf", pdf_bytes("2"))
    await upload_one(admin, "budget.xlsx", xlsx_bytes("3"))
    await upload_one(admin, "100%_done.pdf", pdf_bytes("4"))

    async def ids(**params: str) -> list[str]:
        resp = await client.get("/api/v1/documents", headers=admin.headers, params=params)
        assert resp.status_code == 200, resp.text
        return [d["original_filename"] for d in resp.json()["items"]]

    assert sorted(await ids(q="report")) == ["Q1 report.pdf", "Q2 report.pdf"]
    assert await ids(file_type="xlsx") == ["budget.xlsx"]
    assert await ids(category_id=category["id"]) == ["Q1 report.pdf"]
    assert await ids(q="%") == ["100%_done.pdf"]  # LIKE wildcards are escaped
    assert await ids(sort="title") == ["100%_done.pdf", "budget.xlsx", "Q1 report.pdf", "Q2 report.pdf"]
    assert await ids(sort="-title", page_size="2") == ["Q2 report.pdf", "Q1 report.pdf"]
    page2 = await client.get(
        "/api/v1/documents", headers=admin.headers, params={"sort": "title", "page_size": 2, "page": 2}
    )
    assert page2.json()["total"] == 4 and len(page2.json()["items"]) == 2
