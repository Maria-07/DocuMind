"""Reject oversized request bodies before they are parsed.

FastAPI parses multipart bodies before endpoint dependencies run, so the size check has to live
in ASGI middleware. Enforced on the declared Content-Length *and* on the bytes actually received.
"""

from __future__ import annotations

import json

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(HTTPException):
    """Raised from ``receive`` while the app reads the body.

    An HTTPException, so the framework's exception middleware turns it into a 413 response
    (FastAPI's body parser re-raises HTTPExceptions unchanged). If it ever escapes the app
    before a response starts, the middleware below replies 413 itself.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large")


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, default_limit: int, upload_limit: int, upload_path_suffix: str) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit
        self.upload_path_suffix = upload_path_suffix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return

        is_upload = scope.get("path", "").endswith(self.upload_path_suffix)
        limit = self.upload_limit if is_upload else self.default_limit

        content_length: int | None = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    await _reply(send, 400, "bad_request", "Invalid Content-Length header")
                    return
                break

        if content_length is not None and content_length > limit:
            await _reply(send, 413, "payload_too_large", "Request body is too large")
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _reply(send, 413, "payload_too_large", "Request body is too large")


async def _reply(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "details": None, "request_id": None}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
