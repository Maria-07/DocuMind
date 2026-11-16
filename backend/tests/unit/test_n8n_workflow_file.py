"""The importable n8n workflow stays consistent with the backend it talks to."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "infrastructure" / "n8n" / "documind-document-processing.workflow.json"
COMPOSE = ROOT / "infrastructure" / "docker-compose.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:  # type: ignore[type-arg]
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_is_named_and_importable(workflow: dict) -> None:  # type: ignore[type-arg]
    assert workflow["name"] == "DocuMind - Document Processing Automation"
    assert workflow["active"] is False  # activated deliberately after import
    names = [n["name"] for n in workflow["nodes"]]
    assert len(names) == len(set(names)), "node names must be unique"
    ids = [n["id"] for n in workflow["nodes"]]
    assert len(ids) == len(set(ids))


def test_connections_only_reference_existing_nodes(workflow: dict) -> None:  # type: ignore[type-arg]
    names = {n["name"] for n in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names, f"{source} -> {target['node']}"


def test_every_node_is_reachable_from_the_webhook(workflow: dict) -> None:  # type: ignore[type-arg]
    graph = {s: [t["node"] for b in o["main"] for t in b] for s, o in workflow["connections"].items()}
    seen: set[str] = set()
    todo = ["Webhook - Document Uploaded"]
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node)
            todo += graph.get(node, [])
    unreachable = {n["name"] for n in workflow["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"} - seen
    assert not unreachable, unreachable


def test_webhook_path_matches_the_backend_default(workflow: dict) -> None:  # type: ignore[type-arg]
    webhook = next(n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    default_url = Settings.model_fields["n8n_webhook_url"].default
    assert urlparse(default_url).path == "/webhook/" + webhook["parameters"]["path"]
    assert webhook["parameters"]["httpMethod"] == "POST"


def test_internal_calls_use_the_documented_endpoints(workflow: dict) -> None:  # type: ignore[type-arg]
    urls = {n["name"]: n["parameters"]["url"] for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"}
    assert "/internal/documents/" in urls["Start Processing"] and urls["Start Processing"].endswith("/process")
    assert "/internal/jobs/" in urls["Check Job Status"] and urls["Check Job Status"].endswith("/status")
    assert urls["Retry Job"].endswith("/retry")
    for name in ("Record Success", "Record Failure", "Record Exhausted"):
        assert urls[name].endswith("/workflow-status")


def test_secrets_come_from_the_environment_never_from_the_file(workflow: dict) -> None:  # type: ignore[type-arg]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "$env.DOCUMIND_INTERNAL_API_KEY" in text and "$env.DOCUMIND_WEBHOOK_SECRET" in text
    assert "credentials" not in text  # nothing to create after importing
    # No long random-looking literal (a pasted key) anywhere; node ids are hyphenated UUIDs, which this skips.
    assert not re.search(r"(?<![0-9a-f-])[0-9a-f]{32,}(?![0-9a-f-])", text)
    # Every internal call sends the key; the notification call (to a third party) must not.
    for node in workflow["nodes"]:
        if node["type"] != "n8n-nodes-base.httpRequest":
            continue
        sends_key = "X-Internal-Api-Key" in json.dumps(node["parameters"])
        assert sends_key == (node["name"] != "Send Notification"), node["name"]


def _strings(value: object):  # type: ignore[no-untyped-def]
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_expressions_are_single_line_javascript(workflow: dict) -> None:  # type: ignore[type-arg]
    """A raw newline inside a quoted string in an expression makes n8n fail with "invalid syntax" at run time
    (and "continue on error" then hides it), so expressions must not contain line breaks."""
    for node in workflow["nodes"]:
        if node["type"] == "n8n-nodes-base.code":
            continue  # code nodes are multi-line programs
        for text in _strings(node["parameters"]):
            if text.startswith("="):
                assert not any(ch in text for ch in (chr(10), chr(13))), f"{node['name']}: {text[:80]!r}"


def test_notification_failures_cannot_stop_processing(workflow: dict) -> None:  # type: ignore[type-arg]
    nodes = {n["name"]: n for n in workflow["nodes"]}
    assert nodes["Send Notification"]["onError"] == "continueRegularOutput"
    # The outcome is recorded before anything is sent, and recording problems do not block the notice either.
    for name in ("Record Success", "Record Failure", "Record Exhausted"):
        assert nodes[name]["onError"] == "continueRegularOutput"
    assert nodes["Send Email"]["onError"] == "continueRegularOutput"
    assert "credentials" not in nodes["Send Email"]  # the SMTP login is created in n8n, never stored in the file


def test_compose_runs_n8n_only_under_a_profile_and_publishes_it_locally() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("\n  n8n:") : text.index("\nnetworks:")]
    assert "profiles: [automation]" in block
    assert '"127.0.0.1:${N8N_LOCAL_PORT:-5678}:5678"' in block  # never published on all interfaces
    assert "n8n-data:/home/node/.n8n" in block  # persistent
    assert "N8N_ENCRYPTION_KEY" in block and "N8N_BLOCK_ENV_ACCESS_IN_NODE" in block
    assert re.search(r"image: n8nio/n8n:\d+\.\d+\.\d+", block), "pin the n8n version"
