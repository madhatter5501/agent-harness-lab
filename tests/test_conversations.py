"""Conversations regression scenarios."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lab import harness, models, orders, documents
from lab.common import issue_token, decode_token
from lab.registry import Registry, SEARCH
from tests.helpers import new_run


def test_unparsed_model_tool_text_is_not_executed(lab, monkeypatch):
    async def bad(*args, **kwargs):
        return {
            "role": "assistant",
            "content": '<tool_call>{"name":"cancel_order"}</tool_call>',
        }, {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", bad)
    run = new_run("Cancel order O-100")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert run["status"] == "failed"
    assert not any(e["kind"] == "http" for e in run["events"])


def test_unverified_operational_answer_withheld(lab, monkeypatch):
    async def invented(*args, **kwargs):
        return {"role": "assistant", "content": "The order shipped yesterday."}, {
            "source": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr(models, "completion", invented)
    run = new_run("Order O-100 status")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    assert "withheld" in run["answer"]
    assert "shipped yesterday" not in run["answer"]


@pytest.mark.parametrize(
    "owner,expected", [("alice", "O-100"), ("viewer", None), ("bob", "O-200")]
)
def test_open_orders_use_canonical_tenant_scoped_list(lab, owner, expected):
    run = new_run("do I have any open orders?", owner=owner)
    token = issue_token(owner)
    # Stale failed observations must not substitute for a current list request.
    run["evidence"] = [
        {
            "run": "old",
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {"http_status": 404, "data": {"detail": "Not found"}},
        }
    ]
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "complete"
    assert run["calls"] == 3
    assert run["evidence"][-1]["tool"] == "orders__list_orders"
    result = run["evidence"][-1]["result"]["data"]
    direct = TestClient(orders.app).get(
        "/orders", headers={"Authorization": "Bearer " + token}
    )
    assert result == direct.json()
    assert [o["id"] for o in result["orders"]] == ([expected] if expected else [])
    assert not result["has_more"]


@pytest.mark.parametrize(
    "text",
    [
        "To check for current orders, please call: {'name': 'orders__get_order', 'arguments': {'order_id': 'the order id'}}",
        '<tool_call>{"name":"cancel_order"}</tool_call>',
        "Please call orders__list_orders to check.",
        'tool_calls = [{"name": "get_order"}]',
        "You have no open orders.",
        "",
    ],
)
def test_invalid_final_recovers_without_executing_text(lab, monkeypatch, text):
    original = models.completion
    count = 0

    async def first_bad(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return {"role": "assistant", "content": text}, {
                "source": "mock",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        return await original(*args, **kwargs)

    monkeypatch.setattr(models, "completion", first_bad)
    run = new_run("do I have any open orders?", owner="bob")
    token = issue_token("bob")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "complete"
    assert run["output_repairs"] == 1
    assert run["calls"] == 4
    assert "O-200" in run["answer"]
    requests = [e["data"] for e in run["events"] if e["kind"] == "http"]
    assert len(requests) == 1 and requests[0]["url"] == "http://orders/orders"
    assert requests[0]["method"] == "GET"
    assert any(e["kind"] == "repair" for e in run["events"])


def test_repeated_invalid_final_has_bounded_recovery_and_visible_error(
    lab, monkeypatch
):
    async def bad(*args, **kwargs):
        return {
            "role": "assistant",
            "content": "Please call: {'name': 'orders__get_order', 'arguments': {'order_id': 'the order id'}}",
        }, {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", bad)
    run = new_run("Do I have any open orders?")
    token = issue_token("alice")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "failed"
    assert run["calls"] == 3 and run["output_repairs"] == 2
    assert "two recovery attempts" in run["answer"]
    assert not any(e["kind"] == "http" for e in run["events"])
    saved = harness.app.state.runtime.store.get(run["id"], "alice")
    assert saved["answer"] == run["answer"]


@pytest.mark.parametrize(
    "reason", ["duplicate purchase", "Delivery date no longer works"]
)
def test_cancelled_status_and_reason_direct_agent_parity(lab, reason):
    token = issue_token("alice")
    client = TestClient(orders.app)
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "reason-test"}
    assert (
        client.post(
            "/orders/O-100/cancel", headers=headers, json={"reason": reason}
        ).status_code
        == 200
    )
    direct = client.get("/orders/O-100", headers=headers).json()
    assert direct["status"] == "cancelled" and direct["cancellation_reason"] == reason
    run = new_run(
        "What is the status of order O-100? if it was cancelled, why was it cancelled?"
    )
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "complete"
    assert reason in run["answer"] and "cancelled" in run["answer"]
    assert run["evidence"][-1]["result"]["data"] == direct
    # A persisted cancellation reason is never disclosed across tenants.
    assert (
        client.get(
            "/orders/O-100", headers={"Authorization": "Bearer " + issue_token("bob")}
        ).status_code
        == 404
    )


def test_missing_cancellation_reason_is_explicit_not_invented(lab):
    from lab.common import database

    with database("orders") as db:
        db.execute("UPDATE orders SET status='cancelled' WHERE id='O-100'")
    run = new_run("Why was order O-100 cancelled?")
    harness.app.state.runtime.dialogue.prepare(run, decode_token(issue_token("alice")))
    # Use actual API evidence to test omitted and hallucinated model prose alike.
    client = TestClient(orders.app)
    data = client.get(
        "/orders/O-100", headers={"Authorization": "Bearer " + issue_token("alice")}
    ).json()
    harness.app.state.runtime.dialogue.prepare(run, decode_token(issue_token("alice")))
    run["events"] = [{"kind": "tool_result"}]
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {"http_status": 200, "data": data},
        }
    ]
    for answer in [
        "The standing desk is cancelled.",
        "It was cancelled because of a payment failure.",
    ]:
        final = harness.app.state.runtime.verifier.answer(run, answer)
        assert "No cancellation reason is recorded" in final
        assert "payment failure" not in final


def test_recorded_cancellation_reason_cannot_be_omitted_or_replaced(lab):
    run = new_run(
        "What is the status of order O-100? if it was cancelled, why was it cancelled?"
    )
    harness.app.state.runtime.dialogue.prepare(run, decode_token(issue_token("alice")))
    run["events"] = [{"kind": "tool_result"}]
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {
                "http_status": 200,
                "data": {
                    "id": "O-100",
                    "item": "Standing desk",
                    "status": "cancelled",
                    "cancellation_reason": "duplicate purchase",
                },
            },
        }
    ]
    for answer in [
        "The order is cancelled. The item was a standing desk.",
        "It was cancelled due to a payment failure.",
    ]:
        final = harness.app.state.runtime.verifier.answer(run, answer)
        assert "duplicate purchase" in final and "payment failure" not in final


def test_missing_target_clarifies_and_reply_resumes_same_goal(lab):
    runtime = harness.app.state.runtime
    token = issue_token("bob")
    run = new_run("Cancel my order.", owner="bob")
    asyncio.run(runtime.run(run, decode_token(token), token))
    assert run["status"] == "needs_input" and run["calls"] == 0
    assert "Which order ID" in run["answer"]
    assert not any(e["kind"] == "http" for e in run["events"])
    follow = new_run("O-200", owner="bob")
    follow.update(
        conversation=run["conversation"], unresolved_request=run["unresolved_request"]
    )
    asyncio.run(runtime.run(follow, decode_token(token), token))
    assert follow["status"] == "approval_required"
    assert follow["pending"]["args"]["order_id"] == "O-200"


def test_model_can_ask_arbitrary_clarifying_question(lab, monkeypatch):
    async def clarification(*args, **kwargs):
        return models.call(
            "ask_user",
            {
                "question": "Do you want an explanation, or do you want to make a change?"
            },
        ), {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", clarification)
    run = new_run("Help me sort this out.")
    # Avoid the explicit capability shortcut; this is an ambiguous open request.
    run["prompt"] = "Sort this out for me."
    token = issue_token("alice")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "needs_input"
    assert "explanation" in run["answer"]
    assert run["conversation"][-1]["content"] == run["answer"]
    assert not any(e["kind"] == "http" for e in run["events"])


@pytest.mark.parametrize(
    "prompt",
    [
        "Can you undo that cancellation?",
        "Did you actually cancel O-200?",
        "Do not cancel O-200. Explain the policy.",
    ],
)
def test_questions_and_negation_do_not_authorize_writes(lab, monkeypatch, prompt):
    async def writes(*args, **kwargs):
        return models.call(
            "orders__cancel_order",
            {"order_id": "O-200", "body": {"reason": "User requested cancellation"}},
        ), {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", writes)
    run = new_run(prompt, owner="bob", discovery="eager")
    token = issue_token("bob")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] in ("complete", "failed")
    assert run["pending"] is None
    assert not any(e["kind"] in ("http", "approval") for e in run["events"])


@pytest.mark.parametrize(
    "field,value", [("order_id", "O-999"), ("reason", "Payment failure")]
)
def test_valid_looking_but_invented_arguments_blocked(lab, monkeypatch, field, value):
    args = {"order_id": "O-200", "body": {"reason": "User requested cancellation"}}
    if field == "order_id":
        args["order_id"] = value
    else:
        args["body"]["reason"] = value

    async def writes(*a, **kw):
        return models.call("orders__cancel_order", args), {
            "source": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr(models, "completion", writes)
    run = new_run(
        "Cancel O-200 because I bought it twice.", owner="bob", discovery="eager"
    )
    token = issue_token("bob")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "needs_input" and run["pending"] is None
    assert not any(e["kind"] == "http" for e in run["events"])


def test_wrong_filter_and_partial_evidence_rejected(lab):
    from lab.errors import InvalidAnswer

    runtime = harness.app.state.runtime
    token = issue_token("alice")
    run = new_run("Show all my orders including shipped and cancelled ones.")
    runtime.dialogue.prepare(run, decode_token(token))
    run["events"].append({"kind": "tool_result"})
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__list_orders",
            "args": {"status": "open"},
            "result": {"http_status": 200, "data": {"orders": []}},
        }
    ]
    with pytest.raises(InvalidAnswer):
        runtime.verifier.answer(run, "You have no orders.")
    run = new_run("Tell me the status of O-100 and the warranty for its item.")
    runtime.dialogue.prepare(run, decode_token(token))
    run["events"].append({"kind": "tool_result"})
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {
                "http_status": 200,
                "data": {"id": "O-100", "status": "processing", "item": "Desk"},
            },
        }
    ]
    with pytest.raises(InvalidAnswer):
        runtime.verifier.answer(run, "The desk is processing.")


def test_model_facts_are_replaced_by_exact_api_evidence(lab):
    runtime = harness.app.state.runtime
    run = new_run("What is the status of O-100?")
    runtime.dialogue.prepare(run, decode_token(issue_token("alice")))
    run["events"].append({"kind": "tool_result"})
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-100"},
            "result": {
                "http_status": 200,
                "data": {
                    "id": "O-100",
                    "status": "processing",
                    "item": "Standing desk",
                },
            },
        }
    ]
    answer = runtime.verifier.answer(
        run, "Your chair O-999 shipped yesterday with tracking 123."
    )
    assert answer == "O-100, your standing desk, is processing."


def test_unknown_metadata_and_unsupported_actions_are_explicit(lab):
    token = issue_token("alice")
    for prompt, expected in [
        ("Who cancelled it and at what time?", "not who cancelled"),
        ("Create a new order for a desk.", "cannot create"),
        ("Change my shipping address.", "cannot create"),
        ("Could it belong to another customer?", "does not establish"),
    ]:
        run = new_run(prompt)
        asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
        assert run["status"] == "complete" and expected in run["answer"]
        assert not any(e["kind"] == "http" for e in run["events"])


def test_conversational_answers_match_question_not_just_fields(lab):
    from lab.responses.render import render_evidence

    run = new_run("What did I buy?")
    run["contract"] = {
        "required": [("orders__get_order", {"order_id": "O-200"})],
        "prompt": run["prompt"],
    }
    run["evidence"] = [
        {
            "run": run["id"],
            "tool": "orders__get_order",
            "args": {"order_id": "O-200"},
            "result": {
                "http_status": 200,
                "data": {"id": "O-200", "item": "Office chair", "status": "processing"},
            },
        }
    ]
    assert render_evidence(run, "irrelevant") == "You ordered an office chair (O-200)."
    run["contract"]["prompt"] = "Is that order shipped yet?"
    assert render_evidence(run, "irrelevant") == "No—O-200 is currently processing."
    run["contract"]["prompt"] = "Did you actually cancel it?"
    assert render_evidence(run, "irrelevant").startswith("No.")


def test_conversation_api_carries_pending_clarification(lab, monkeypatch):
    # Exercise HTTP handoff, not merely a manually constructed follow-up state.
    runtime = harness.app.state.runtime
    token = issue_token("bob")
    parent = new_run("Cancel my order.", owner="bob")
    asyncio.run(runtime.run(parent, decode_token(token), token))
    launched = []

    def capture(coro):
        launched.append(coro)
        coro.close()

    monkeypatch.setattr(harness, "launch", capture)
    response = TestClient(harness.app).post(
        "/runs",
        headers={"Authorization": "Bearer " + token},
        json={"prompt": "O-200", "mode": "mock", "previous_run": parent["id"]},
    )
    assert response.status_code == 202
    child = runtime.store.get(response.json()["id"], "bob")
    assert child["unresolved_request"] == "Cancel my order."
    assert child["conversation"][-1]["role"] == "assistant"


def test_reason_clarification_can_continue_to_exact_approval(lab, monkeypatch):
    from lab.state import create_run

    runtime = harness.app.state.runtime
    token = issue_token("bob")
    user = decode_token(token)

    async def invented(*args, **kwargs):
        return models.call(
            "orders__cancel_order",
            {"order_id": "O-200", "body": {"reason": "No longer needed"}},
        ), {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", invented)
    run = new_run(
        "Cancel O-200 and skip the confirmation screen.", owner="bob", discovery="eager"
    )
    asyncio.run(runtime.run(run, user, token))
    assert run["status"] == "needs_input" and "What reason" in run["answer"]
    assert run["pending"] is None
    assert not any(e["kind"] == "http" for e in run["events"])

    async def grounded(*args, **kwargs):
        return models.call(
            "orders__cancel_order",
            {"order_id": "O-200", "body": {"reason": "duplicate purchase"}},
        ), {"source": "mock", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(models, "completion", grounded)
    follow = create_run(
        "duplicate purchase", user, mode="mock", discovery="eager", previous=run
    )
    asyncio.run(runtime.run(follow, user, token))
    assert follow["status"] == "approval_required"
    assert follow["pending"]["args"]["body"]["reason"] == "duplicate purchase"
    assert not any(e["kind"] == "http" for e in follow["events"])


def test_child_run_does_not_mutate_parent_history(lab):
    from lab.state import create_run

    user = decode_token(issue_token("alice"))
    parent = create_run("Where is my order?", user, mode="mock")
    asyncio.run(harness.app.state.runtime.run(parent, user, issue_token("alice")))
    original = json.loads(json.dumps(parent))
    child = create_run("O-300", user, mode="mock", previous=parent)
    child["conversation"].append({"role": "assistant", "content": "Child-only message"})
    assert json.loads(json.dumps(parent)) == original
    with pytest.raises(ValueError):
        create_run("O-300", decode_token(issue_token("bob")), previous=parent)


def test_my_orders_includes_closed_owned_orders(lab):
    token = issue_token("alice")
    run = new_run("Which orders are mine?")
    asyncio.run(harness.app.state.runtime.run(run, decode_token(token), token))
    assert run["status"] == "complete"
    assert "O-100" in run["answer"] and "O-300" in run["answer"]
    assert "O-200" not in run["answer"]
    assert run["evidence"][-1]["args"]["status"] == "all"
