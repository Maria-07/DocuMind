from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import ChatServiceDep, CurrentPrincipal, rate_limit_by_user
from app.api.sse import sse_response
from app.schemas.chat import (
    AnswerResponse,
    ChatModelsResponse,
    CitationRead,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
    SourceRead,
)
from app.schemas.common import ERROR_RESPONSES, Page, PageParams

router = APIRouter(prefix="/chat", tags=["chat"], responses=ERROR_RESPONSES)


@router.get("/models", response_model=ChatModelsResponse, summary="Models the caller can pick, with prices")
async def list_models(principal: CurrentPrincipal, service: ChatServiceDep) -> ChatModelsResponse:
    return service.list_models()


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreate, principal: CurrentPrincipal, service: ChatServiceDep
) -> ConversationRead:
    return ConversationRead.model_validate(await service.create(principal, data))


@router.get("/conversations", response_model=Page[ConversationRead], summary="The caller's conversations")
async def list_conversations(
    principal: CurrentPrincipal, service: ChatServiceDep, page: Annotated[PageParams, Depends()]
) -> Page[ConversationRead]:
    items, total = await service.list(principal, page)
    return Page(
        items=[ConversationRead.model_validate(c) for c in items], total=total, page=page.page, page_size=page.page_size
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, service: ChatServiceDep
) -> ConversationDetail:
    conv, messages = await service.get(principal, conversation_id)
    return ConversationDetail(**ConversationRead.model_validate(conv).model_dump(), messages=messages)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead, summary="Rename / change scope")
async def update_conversation(
    conversation_id: uuid.UUID, data: ConversationUpdate, principal: CurrentPrincipal, service: ChatServiceDep
) -> ConversationRead:
    return ConversationRead.model_validate(await service.update(principal, conversation_id, data))


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, service: ChatServiceDep
) -> Response:
    await service.delete(principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AnswerResponse,
    dependencies=[Depends(rate_limit_by_user("chat", "rate_limit_chat_per_minute"))],
    summary="Ask a question; streams Server-Sent Events when Accept: text/event-stream",
    description=(
        "SSE events: `meta` (ids, model) · `status` (thinking|retrieving|generating) · `sources` (retrieved "
        "chunks) · `token` (answer text, including [n] citation markers) · `citation` (a newly cited source) · "
        "`done` (final citations, grounded flag, route, model, usage incl. estimated cost) · `error`. Set `model` "
        "to choose a model from GET /chat/models; set `edit_message_id` to edit and resubmit an earlier message. "
        "Without the SSE Accept header the complete answer is returned as JSON."
    ),
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    principal: CurrentPrincipal,
    service: ChatServiceDep,
) -> StreamingResponse | AnswerResponse:
    ask = await service.prepare_ask(
        principal,
        conversation_id,
        data.content,
        data.scope.as_dict() if data.scope is not None else None,
        model_id=data.model,
        edit_message_id=data.edit_message_id,
    )
    if "text/event-stream" in request.headers.get("accept", ""):
        return sse_response(service.stream_answer(ask))

    content, sources, citations, status_value = "", [], [], "complete"
    async for event in service.stream_answer(ask):
        if event.name == "token":
            content += event.data["text"]
        elif event.name == "sources":
            sources = [SourceRead.model_validate(s) for s in event.data["sources"]]
        elif event.name == "done":
            citations = [CitationRead.model_validate(c) for c in event.data["citations"]]
        elif event.name == "error":
            status_value = "error"
    _, messages = await service.get(principal, conversation_id)
    message = next(m for m in messages if m.id == ask.assistant_message_id)
    return AnswerResponse(
        conversation_id=conversation_id,
        message=MessageRead.model_validate(message).model_copy(
            update={"content": content.strip() or message.content, "citations": citations, "status": status_value}
        ),
        sources=sources,
    )
