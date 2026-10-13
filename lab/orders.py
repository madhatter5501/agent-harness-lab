"""The ONLY implementation of order rules. Humans and agents call these routes."""

import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path as ApiPath
from pydantic import BaseModel, Field

from .common import database, principal, require


def initialize():
    with database("orders") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, tenant TEXT, status TEXT, item TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, fingerprint TEXT, result TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, operation TEXT, resource TEXT)"
        )
        if "owner" not in {
            row["name"] for row in db.execute("PRAGMA table_info(orders)")
        }:
            db.execute("ALTER TABLE orders ADD COLUMN owner TEXT")
        # Migrate only the known demo fixtures; never guess ownership of other rows.
        for order_id, owner in [
            ("O-100", "alice"),
            ("O-300", "alice"),
            ("O-200", "bob"),
        ]:
            db.execute(
                "UPDATE orders SET owner=? WHERE id=? AND owner IS NULL",
                (owner, order_id),
            )
        db.executemany(
            "INSERT OR IGNORE INTO orders(id,tenant,status,item,owner) VALUES (?,?,?,?,?)",
            [
                ("O-100", "acme", "processing", "Standing desk", "alice"),
                ("O-300", "acme", "shipped", "Desk lamp", "alice"),
                ("O-200", "beta", "processing", "Office chair", "bob"),
            ],
        )


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="Orders — canonical API", lifespan=lifespan)


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


def owned_order(db, order_id, user):
    row = db.execute(
        "SELECT * FROM orders WHERE id=? AND tenant=? AND owner=?",
        (order_id, user["tenant"], user["sub"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Order not found in your account")
    return dict(row)


@app.get(
    "/orders",
    operation_id="list_orders",
    summary="List orders owned by the signed-in user, not all tenant orders. No order ID needed. Open means processing; all includes shipped and cancelled.",
    openapi_extra={"x-agent": {"scope": "orders:read", "effect": "read"}},
)
def list_orders(status: Literal["open", "all"] = "open", user=Depends(principal)):
    require(user, "orders:read")
    with database("orders") as db:
        rows = db.execute(
            "SELECT id,status,item FROM orders WHERE tenant=? AND owner=? AND (?='all' OR status='processing') ORDER BY id LIMIT 101",
            (user["tenant"], user["sub"], status),
        ).fetchall()
    return {
        "orders": [dict(row) for row in rows[:100]],
        "has_more": len(rows) > 100,
        "filter": status,
        "scope": "current_user",
    }


@app.get(
    "/orders/{order_id}",
    operation_id="get_order",
    summary="Look up current status, item, and recorded cancellation reason by order ID.",
    openapi_extra={"x-agent": {"scope": "orders:read", "effect": "read"}},
)
def get_order(order_id: str = ApiPath(pattern=r"^O-[0-9]+$"), user=Depends(principal)):
    require(user, "orders:read")
    with database("orders") as db:
        result = owned_order(db, order_id, user)
        result["cancellation_reason"] = None
        if result["status"] == "cancelled":
            # The idempotency ledger is the existing durable cancellation record.
            # Tenant binding prevents an order ID from revealing another tenant's reason.
            cancellation = db.execute(
                "SELECT result FROM requests WHERE json_extract(result, '$.id')=? "
                "AND json_extract(fingerprint, '$[1]')=? ORDER BY rowid DESC LIMIT 1",
                (order_id, user["tenant"]),
            ).fetchone()
            if cancellation:
                result["cancellation_reason"] = json.loads(cancellation["result"]).get(
                    "reason"
                )
    result.pop("tenant")
    result.pop("owner", None)
    return result


class CancelOrder(BaseModel):
    reason: str = Field(
        min_length=3, max_length=160, description="The user's reason for cancellation"
    )


@app.post(
    "/orders/{order_id}/cancel",
    operation_id="cancel_order",
    summary="Cancel an unshipped order. Requires explicit user confirmation in the agent UI.",
    openapi_extra={"x-agent": {"scope": "orders:write", "effect": "write"}},
)
def cancel_order(
    body: CancelOrder,
    order_id: str = ApiPath(pattern=r"^O-[0-9]+$"),
    user=Depends(principal),
    idempotency_key: str = Header(min_length=1, max_length=150),
):
    require(user, "orders:write")
    fingerprint = json.dumps([user["sub"], user["tenant"], order_id, body.reason])
    key = user["sub"] + ":" + idempotency_key
    with database("orders") as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM requests WHERE key=?", (key,)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Idempotency key was already used for a different request"
                )
            return json.loads(previous["result"])
        order = owned_order(db, order_id, user)
        if order["status"] == "shipped":
            raise HTTPException(409, "A shipped order cannot be cancelled")
        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        result = {"id": order_id, "status": "cancelled", "reason": body.reason}
        db.execute(
            "INSERT INTO requests VALUES (?,?,?)",
            (key, fingerprint, json.dumps(result)),
        )
        db.execute(
            "INSERT INTO audit(actor,operation,resource) VALUES (?,?,?)",
            (user["sub"], "cancel", order_id),
        )
        return result
