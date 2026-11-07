"""Server-Sent Events encoding with heartbeats."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

from app.services.chat import ChatEvent

HEARTBEAT_SECONDS = 15.0
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
    "Connection": "keep-alive",
}


def encode(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


async def with_heartbeats(
    events: AsyncIterator[ChatEvent], interval: float = HEARTBEAT_SECONDS
) -> AsyncIterator[bytes]:
    """Encode events; emit a comment line while idle so proxies don't drop the connection.

    The pending ``__anext__`` is never cancelled by a heartbeat (only awaited again), so slow
    upstream steps — retrieval, model thinking — are not interrupted.
    """
    iterator = events.__aiter__()
    pending: asyncio.Task[ChatEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield b": ping\n\n"
                continue
            task, pending = pending, None
            try:
                event = task.result()
            except StopAsyncIteration:
                return
            yield encode(event.name, event.data)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


def sse_response(events: AsyncIterator[ChatEvent]) -> StreamingResponse:
    return StreamingResponse(with_heartbeats(events), media_type="text/event-stream", headers=SSE_HEADERS)
