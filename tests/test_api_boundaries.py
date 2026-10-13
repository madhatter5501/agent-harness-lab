"""Api boundaries regression scenarios."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lab import harness, models, orders, documents
from lab.common import issue_token, decode_token
from lab.registry import Registry, SEARCH
from tests.helpers import new_run


def test_permissions_and_tenant_boundaries(lab):
    viewer = decode_token(issue_token("viewer"))
    assert "orders__cancel_order" not in [t.name for t in lab.allowed(viewer)]
    client = TestClient(orders.app)
    assert (
        client.get(
            "/orders/O-200", headers={"Authorization": "Bearer " + issue_token("alice")}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/orders/O-100/cancel",
            json={"reason": "test cancellation"},
            headers={
                "Authorization": "Bearer " + issue_token("viewer"),
                "Idempotency-Key": "test",
            },
        ).status_code
        == 403
    )
    assert client.get("/orders/O-100").status_code == 403


def test_business_rules_identical_for_agent(lab):
    run = new_run("Cancel order O-300")
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice")
        )
    )
    asyncio.run(
        harness.app.state.runtime.run(
            run, decode_token(issue_token("alice")), issue_token("alice"), approved=True
        )
    )
    assert any(
        e["kind"] == "tool_result" and e["data"]["http_status"] == 409
        for e in run["events"]
    )


def test_schema_rejects_extra_fields_and_path_injection(lab):
    tool = lab.tools["orders__get_order"]
    with pytest.raises(ValueError):
        tool.request({"order_id": "O-100", "url": "http://attacker"})
    with pytest.raises(ValueError):
        tool.request({"order_id": "../admin"})


def test_list_auth_filter_empty_and_bounded_results(lab):
    from lab.common import database

    client = TestClient(orders.app)
    headers = {"Authorization": "Bearer " + issue_token("alice")}
    assert client.get("/orders").status_code == 403
    assert client.get("/orders?status=invalid", headers=headers).status_code == 422
    assert len(client.get("/orders?status=all", headers=headers).json()["orders"]) == 2
    with database("orders") as db:
        db.execute("UPDATE orders SET status='cancelled' WHERE tenant='acme'")
    assert client.get("/orders", headers=headers).json()["orders"] == []
    with database("orders") as db:
        db.executemany(
            "INSERT INTO orders(id,tenant,status,item) VALUES (?,?,?,?)",
            [("O-" + str(1000 + i), "acme", "processing", "Item") for i in range(101)],
        )
        db.execute("UPDATE orders SET owner='alice' WHERE owner IS NULL")
    result = client.get("/orders", headers=headers).json()
    assert len(result["orders"]) == 100 and result["has_more"]


@pytest.mark.parametrize("operation", ["get_order", "cancel_order"])
def test_placeholder_id_rejected_by_both_tool_schema_and_canonical_api(lab, operation):
    args = {"order_id": "the order id"}
    if operation == "cancel_order":
        args["body"] = {"reason": "User requested cancellation"}
    with pytest.raises(ValueError):
        lab.tools["orders__" + operation].validate(args)
    client = TestClient(orders.app)
    headers = {
        "Authorization": "Bearer " + issue_token("alice"),
        "Idempotency-Key": "placeholder",
    }
    response = (
        client.get("/orders/the%20order%20id", headers=headers)
        if operation == "get_order"
        else client.post(
            "/orders/the%20order%20id/cancel", headers=headers, json=args["body"]
        )
    )
    assert response.status_code == 422


def test_list_requires_read_scope(lab):
    from lab.common import principal

    user = {"sub": "restricted", "tenant": "acme", "scopes": []}
    assert "orders__list_orders" not in [t.name for t in lab.allowed(user)]
    orders.app.dependency_overrides[principal] = lambda: user
    try:
        assert TestClient(orders.app).get("/orders").status_code == 403
    finally:
        orders.app.dependency_overrides.pop(principal)


def test_policy_search_no_irrelevant_default_results(lab):
    client = TestClient(documents.app)
    headers = {"Authorization": "Bearer " + issue_token("alice")}
    assert (
        client.get(
            "/documents/search", params={"q": "cryptocurrency refunds"}, headers=headers
        ).json()["matches"]
        == []
    )
    docs = client.get(
        "/documents/search", params={"q": "warranty and return window"}, headers=headers
    ).json()["matches"]
    assert {d["id"] for d in docs} == {"warranty", "returns"}


def test_same_tenant_is_not_order_ownership(lab):
    from lab.common import database

    with database("orders") as db:
        db.execute(
            "INSERT INTO orders(id,tenant,status,item,owner) VALUES (?,?,?,?,?)",
            ("O-401", "acme", "processing", "Keyboard", "viewer"),
        )
    client = TestClient(orders.app)
    alice = {"Authorization": "Bearer " + issue_token("alice")}
    viewer = {"Authorization": "Bearer " + issue_token("viewer")}
    assert {
        o["id"]
        for o in client.get("/orders?status=all", headers=alice).json()["orders"]
    } == {"O-100", "O-300"}
    assert {
        o["id"]
        for o in client.get("/orders?status=all", headers=viewer).json()["orders"]
    } == {"O-401"}
    assert client.get("/orders/O-100", headers=viewer).status_code == 404
    assert client.get("/orders/O-401", headers=alice).status_code == 404
    assert (
        client.post(
            "/orders/O-401/cancel",
            headers={**alice, "Idempotency-Key": "ownership"},
            json={"reason": "User requested cancellation"},
        ).status_code
        == 404
    )
