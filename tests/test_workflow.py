"""Workflow regression scenarios."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lab import harness, models, orders, documents
from lab.common import issue_token, decode_token
from lab.registry import Registry, SEARCH
from tests.helpers import new_run


def test_lazy_discovery_and_direct_api_parity(lab):
    run = new_run("What is the status of order O-100?")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert run["status"] == "complete"
    assert run["calls"] == 3
    result = next(
        e["data"]["body"] for e in run["events"] if e["kind"] == "tool_result"
    )
    direct = TestClient(orders.app).get(
        "/orders/O-100", headers={"Authorization": "Bearer " + issue_token("alice")}
    )
    assert result == direct.json()
    first = next(e for e in run["events"] if e["kind"] == "model")
    assert {t["function"]["name"] for t in first["data"]["tools"]} == {
        "find_tools",
        "ask_user",
    }
    assert (
        len(first["data"]["messages"]) == 3
    )  # Audit snapshot did not mutate with later tool calls.


def test_eager_skips_discovery(lab):
    run = new_run("Order O-100 status", discovery="eager")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert run["calls"] == 2
    assert not any(e["kind"] == "discovery" for e in run["events"])


def test_write_waits_and_idempotency(lab):
    token = issue_token("alice")
    run = new_run("Cancel order O-100")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "approval_required"
    assert not any(e["kind"] == "http" for e in run["events"])
    pending = run["pending"]
    asyncio.run(
        harness.app.state.runtime.run(run, decode_token(token), token, approved=True)
    )
    assert run["status"] == "complete"
    client = TestClient(orders.app)
    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": run["id"] + ":" + pending["id"],
    }
    response = client.post(
        "/orders/O-100/cancel", headers=headers, json=pending["args"]["body"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    response = client.post(
        "/orders/O-100/cancel", headers=headers, json={"reason": "Different reason"}
    )
    assert response.status_code == 409


def test_compaction_retains_raw_audit_and_exact_state(lab):
    run = new_run("Order O-100 status")
    run["conversation"] = [
        {"role": "user", "content": "A long conversation " * 400},
        {"role": "assistant", "content": "Older answer"},
        {"role": "user", "content": "Most recent question"},
        {"role": "assistant", "content": "Most recent answer"},
    ]
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert run["summary"]
    assert len(run["conversation"]) == 4  # Last pair plus this turn.
    assert any(e["kind"] == "memory" for e in run["events"])


def test_approval_owner_and_decline(lab):
    from fastapi import HTTPException

    runtime = harness.app.state.runtime
    run = new_run("Cancel order O-100")
    asyncio.run(
        runtime.run(run, decode_token(issue_token("alice")), issue_token("alice"))
    )
    with pytest.raises(HTTPException):
        harness.fetch(run["id"], decode_token(issue_token("bob")))
    runtime.approvals.decide(run, False)
    assert run["status"] == "denied"
    assert not any(e["kind"] == "http" for e in run["events"])
    with pytest.raises(ValueError):
        runtime.approvals.decide(run, True)


def test_approval_checkpoint_survives_new_runtime(lab):
    from lab.runtime import build_runtime

    runtime = harness.app.state.runtime
    token = issue_token("alice")
    run = new_run("Cancel order O-100")
    asyncio.run(runtime.run(run, decode_token(token), token))
    restored = build_runtime(lab)
    restored.store.initialize()
    saved = restored.store.get(run["id"], "alice")
    assert saved["status"] == "approval_required"
    assert saved["pending"] == run["pending"]
    restored.approvals.decide(saved, True)
    asyncio.run(restored.run(saved, decode_token(token), token, approved=True))
    assert saved["status"] == "complete"
