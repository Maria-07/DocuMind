"""n8n integration building blocks: backoff, internal-key check, webhook client, failure classes, settings."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import AuthenticationError
from app.rag.ingestion.pipeline import CODE_NETWORK, CODE_PROCESSING, CODE_VECTOR_STORE, classify_failure
from app.rag.ingestion.types import PermanentIngestionError
from app.services.workflow import (
    UPLOAD_EVENT,
    WEBHOOK_SECRET_HEADER,
    N8nWebhookNotifier,
    UploadEvent,
    backoff_seconds,
    check_internal_key,
)
from tests.helpers import make_settings

KEY = "k" * 32
SECRET = "s" * 32


def n8n_settings(**overrides: object):  # type: ignore[no-untyped-def]
    return make_settings(n8n_enabled=True, internal_api_key=KEY, n8n_webhook_secret=SECRET, **overrides)


def event() -> UploadEvent:
    return UploadEvent(
        event_id="document.uploaded:abc",
        document_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        file_type="pdf",
        size_bytes=1234,
        title="Policy",
        uploaded_by=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
    )


# --- backoff -------------------------------------------------------------------------------------------


def test_backoff_is_exponential_and_capped() -> None:
    s = n8n_settings(n8n_retry_base_seconds=30, n8n_retry_max_seconds=200)
    assert [backoff_seconds(n, s) for n in range(5)] == [30, 60, 120, 200, 200]
    assert backoff_seconds(-1, s) == 30


# --- internal API key --------------------------------------------------------------------------------------


def test_internal_key_must_match_exactly() -> None:
    settings = n8n_settings()
    check_internal_key(settings, KEY)
    for bad in (None, "", "wrong", KEY + "x", KEY[:-1]):
        with pytest.raises(AuthenticationError):
            check_internal_key(settings, bad)


def test_no_configured_key_means_nothing_is_accepted() -> None:
    settings = make_settings()  # n8n off, no key
    for presented in (None, "", "anything"):
        with pytest.raises(AuthenticationError):
            check_internal_key(settings, presented)


# --- settings rules ----------------------------------------------------------------------------------------


def test_enabling_n8n_requires_strong_secrets() -> None:
    make_settings()  # off by default, no secrets needed
    with pytest.raises(ValidationError, match="INTERNAL_API_KEY"):
        make_settings(n8n_enabled=True, n8n_webhook_secret=SECRET)
    with pytest.raises(ValidationError, match="N8N_WEBHOOK_SECRET"):
        make_settings(n8n_enabled=True, internal_api_key=KEY, n8n_webhook_secret="short")
    with pytest.raises(ValidationError, match="N8N_RETRY_MAX_SECONDS"):
        n8n_settings(n8n_retry_base_seconds=100, n8n_retry_max_seconds=10)
    assert n8n_settings().n8n_max_retries == 3


# --- the webhook payload and client ---------------------------------------------------------------------------


def test_webhook_payload_has_ids_and_metadata_only() -> None:
    payload = event().payload()
    assert payload["event"] == UPLOAD_EVENT
    assert set(payload) == {
        "event",
        "event_id",
        "occurred_at",
        "document_id",
        "job_id",
        "organization_id",
        "file_type",
        "size_bytes",
        "title",
        "uploaded_by",
    }
    json.dumps(payload)  # JSON-serializable as is


async def _notifier(handler) -> N8nWebhookNotifier:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return N8nWebhookNotifier(n8n_settings(n8n_webhook_url="http://n8n.test/webhook/x"), client)


async def test_webhook_is_delivered_with_the_secret_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    assert await (await _notifier(handler)).document_uploaded(event()) is True
    assert seen[0].headers[WEBHOOK_SECRET_HEADER] == SECRET
    assert json.loads(seen[0].content)["event"] == UPLOAD_EVENT
    assert str(seen[0].url) == "http://n8n.test/webhook/x"


@pytest.mark.parametrize("status", [404, 500, 403])
async def test_rejected_webhook_is_not_delivered(status: int) -> None:
    notifier = await _notifier(lambda request: httpx.Response(status))
    assert await notifier.document_uploaded(event()) is False


async def test_unreachable_or_slow_n8n_is_not_delivered() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    assert await (await _notifier(refuse)).document_uploaded(event()) is False
    assert await (await _notifier(slow)).document_uploaded(event()) is False


# --- failure classification -------------------------------------------------------------------------------------


def test_failures_are_classified_without_exposing_their_text() -> None:
    from qdrant_client.http.exceptions import ResponseHandlingException

    assert classify_failure(PermanentIngestionError("corrupt_pdf", "x")) == "corrupt_pdf"
    assert classify_failure(ResponseHandlingException(Exception("down"))) == CODE_VECTOR_STORE
    assert classify_failure(httpx.ConnectError("embedding provider unreachable")) == CODE_NETWORK
    assert classify_failure(TimeoutError("slow")) == CODE_NETWORK
    assert classify_failure(ValueError("bug")) == CODE_PROCESSING
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = httpx.ReadTimeout("inner")
    assert classify_failure(wrapped) == CODE_NETWORK
