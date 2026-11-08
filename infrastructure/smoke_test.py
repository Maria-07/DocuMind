"""End-to-end smoke test of a running DocuMind stack, through the public entry point only.

    python infrastructure/smoke_test.py https://localhost --insecure

Checks: the app is served with security headers; API docs are off; registration works; a real DOCX
upload is parsed, chunked, embedded and indexed by the worker; hybrid search finds it; a spoofed
X-Forwarded-For is sent at registration (CI then asserts the audit log did not record it).

Needs only the standard library plus python-docx (to build the test document).
"""

from __future__ import annotations

import argparse
import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


def build_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Smoke Test Policy", level=1)
    doc.add_paragraph("Backups of the customer database are taken every four hours and kept for thirty days.")
    doc.add_paragraph("Incident reports must be filed within two business days.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class Client:
    def __init__(self, base: str, insecure: bool) -> None:
        self.base = base.rstrip("/")
        self.ctx = ssl._create_unverified_context() if insecure else None  # noqa: S323 - opt-in for local CA
        self.token: str | None = None

    def request(
        self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = dict(headers or {})
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=hdrs)  # noqa: S310
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=60) as resp:  # noqa: S310
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()

    def json(self, method: str, path: str, payload: Any = None, headers: dict[str, str] | None = None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        status, _, data = self.request(method, path, body, {"Content-Type": "application/json", **(headers or {})})
        if status >= 400:
            raise AssertionError(f"{method} {path} -> {status}: {data[:300]!r}")
        return json.loads(data) if data else None


def check(cond: bool, msg: str) -> None:
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--insecure", action="store_true", help="accept a self-signed / internal-CA certificate")
    ap.add_argument("--production", action="store_true", help="also assert production-only hardening")
    ap.add_argument("--index-timeout", type=int, default=1200, help="seconds (first run downloads models)")
    args = ap.parse_args()
    c = Client(args.base_url, args.insecure)

    status, headers, _ = c.request("GET", "/login")
    check(status == 200, "frontend serves /login")
    check(headers.get("x-frame-options") == "DENY", "X-Frame-Options: DENY")
    if args.production:
        check("content-security-policy" in headers, "Content-Security-Policy header present")
        status, _, _ = c.request("GET", "/api/v1/openapi.json")
        check(status == 404, "OpenAPI schema is not exposed in production")

    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    reg = c.json(
        "POST",
        "/api/v1/auth/register",
        {"email": email, "password": "Smoke-test-pass-1", "full_name": "Smoke Test", "organization_name": "Smoke"},
        headers={"X-Forwarded-For": "6.6.6.6"},  # must NOT be recorded as the client address
    )
    c.token = reg["access_token"]
    check(bool(c.token), f"registered {email}")

    boundary = uuid.uuid4().hex
    docx = build_docx()
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="Smoke Policy.docx"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
        ).encode()
        + docx
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, _, data = c.request(
        "POST", "/api/v1/documents/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    check(status in (200, 201, 202), f"upload accepted ({status})")
    result = json.loads(data)["results"][0]
    check(result["status"] == "accepted", f"upload result: {result['status']}")
    doc_id = result["document"]["id"]

    deadline = time.time() + args.index_timeout
    state = None
    while time.time() < deadline:
        state = c.json("GET", f"/api/v1/documents/{doc_id}")["status"]
        if state in ("indexed", "failed"):
            break
        time.sleep(5)
    check(state == "indexed", f"worker indexed the document (status={state})")

    hits = c.json("POST", "/api/v1/search/hybrid", {"query": "How long are database backups kept?", "top_k": 5})
    check(any(h["document_id"] == doc_id for h in hits["results"]), "hybrid search finds the uploaded document")
    print(f"registered_email={email}")


if __name__ == "__main__":
    main()
