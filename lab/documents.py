"""A second downstream API, independently discoverable from its OpenAPI document."""

import re
from fastapi import Depends, FastAPI, Query
from .common import principal, require

app = FastAPI(title="Documents — canonical API")
ARTICLES = [
    {
        "id": "shipping",
        "title": "Shipping policy",
        "text": "Standard shipping takes 3–5 business days after dispatch. Tracking appears on the order once shipped.",
    },
    {
        "id": "returns",
        "title": "Returns and cancellation",
        "text": "Unshipped orders can be cancelled. Shipped items require a return request within 30 days of delivery.",
    },
    {
        "id": "warranty",
        "title": "Warranty",
        "text": "Desks and chairs have a two-year warranty. Lamps have a one-year warranty. Keep the order ID for a claim.",
    },
]


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


@app.get(
    "/documents/search",
    operation_id="search_documents",
    summary="Search product warranty, shipping, returns, and cancellation policies.",
    openapi_extra={"x-agent": {"scope": "documents:read", "effect": "read"}},
)
def search_documents(
    q: str = Query(min_length=2, max_length=200), user=Depends(principal)
):
    require(user, "documents:read")
    # Match meaningful concepts, not stopwords such as "the" and "for".
    terms = set(re.findall(r"[a-z]+", q.lower()))
    groups = {
        "shipping": {"shipping", "ship", "delivery", "dispatch", "tracking", "arrive"},
        "returns": {
            "returns",
            "return",
            "cancel",
            "cancellation",
            "cancelled",
            "refund",
            "refunds",
        },
        "warranty": {
            "warranty",
            "warranties",
            "desk",
            "desks",
            "lamp",
            "lamps",
            "chair",
            "chairs",
        },
    }
    unsupported = terms & {"cryptocurrency", "crypto", "bitcoin"}
    matches = [] if unsupported else [a for a in ARTICLES if terms & groups[a["id"]]]
    return {"matches": matches, "source": "canonical-policy-api"}
