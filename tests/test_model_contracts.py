"""Model contracts regression scenarios."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lab import harness, models, orders, documents
from lab.common import issue_token, decode_token
from lab.registry import Registry, SEARCH
from tests.helpers import new_run


def test_unknown_tool_never_executes(lab, monkeypatch):
    async def bad(*args, **kwargs):
        return models.call("delete_everything", {}), {
            "source": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr(models, "completion", bad)
    run = new_run("Order status")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert run["status"] == "failed"
    assert not any(e["kind"] == "http" for e in run["events"])


def test_local_decision_schema_and_provider_body(lab, monkeypatch):
    from jsonschema import validate, ValidationError

    tools = [SEARCH]
    monkeypatch.setattr(models, "PROTOCOL", "chat")
    monkeypatch.setattr(models, "DECISION_FORMAT", "json")
    endpoint, body = models.build_request(
        [
            {"role": "system", "content": "Rules"},
            {"role": "user", "content": "Order status"},
        ],
        tools,
    )
    assert endpoint == "/chat/completions"
    assert "response_format" in body
    assert "find_tools" in body["messages"][0]["content"]
    validate(
        {"kind": "tool", "name": "find_tools", "arguments": {"query": "orders"}},
        models.decision_schema(tools),
    )
    with pytest.raises(ValidationError):
        validate(
            {"kind": "tool", "name": "unknown", "arguments": {}},
            models.decision_schema(tools),
        )


def test_responses_adapter_preserves_reasoning_items(monkeypatch):
    monkeypatch.setattr(models, "PROTOCOL", "responses")
    items = [
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "find_tools",
            "arguments": '{"query":"orders"}',
        },
    ]
    endpoint, body = models.build_request(
        [
            {"role": "user", "content": "Orders"},
            {"role": "assistant", "_responses_output": items},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ],
        [SEARCH],
    )
    assert endpoint == "/responses"
    assert body["input"][1:3] == items
    assert body["input"][3]["call_id"] == "c1"
    assert "max_output_tokens" in body and "temperature" not in body


def test_structured_placeholder_never_reaches_http(lab, monkeypatch):
    async def placeholder(*args, **kwargs):
        return models.call("orders__get_order", {"order_id": "the order id"}), {
            "source": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr(models, "completion", placeholder)
    run = new_run("Do I have any open orders?", discovery="eager")
    token = issue_token("alice")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "failed"
    assert not any(e["kind"] == "http" for e in run["events"])


def test_required_evidence_controls_next_decision_schema(lab):
    from jsonschema import validate, ValidationError

    runtime = harness.app.state.runtime
    run = new_run("What is the status of both O-100 and O-300?")
    runtime.dialogue.prepare(run, decode_token(issue_token("alice")))
    run["loaded"] = ["orders__get_order"]
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {"http_status": 200, "data": {"id": "O-100"}},
        }
    ]
    tools = runtime.tools.definitions(run)
    schema = models.decision_schema(tools, allow_final=False)
    validate(
        {
            "kind": "tool",
            "name": "orders__get_order",
            "arguments": {"order_id": "O-300"},
        },
        schema,
    )
    for decision in [
        {"kind": "final", "answer": "They have shipped."},
        {
            "kind": "tool",
            "name": "orders__get_order",
            "arguments": {"order_id": "O-100"},
        },
    ]:
        with pytest.raises(ValidationError):
            validate(decision, schema)
