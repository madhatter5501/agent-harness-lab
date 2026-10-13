"""Small, explicit request rules for the demo's order and policy vocabulary.

Keep these rules out of the generic loop. They improve local-model reliability,
but are not a general language understanding system. API ownership and scope
checks remain authoritative, even when interpretation is wrong.
"""

import re
from typing import List, Optional, Tuple

from ..state import Identity, RequestContract, RequiredOperation, RunState, RunStatus


def matches(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def order_ids(text: str) -> List[str]:
    return list(
        dict.fromkeys(value.upper() for value in re.findall(r"\bO-\d+\b", text, re.I))
    )


def prior_user_messages(run: RunState) -> List[str]:
    return [
        message["content"]
        for message in run.get("conversation", [])
        if message["role"] == "user"
    ]


def recent_order_ids(run: RunState) -> List[str]:
    for observation in reversed(run.get("evidence", [])):
        result = observation.get("result", {})
        if result.get("http_status") != 200 or not observation["tool"].startswith(
            "orders__"
        ):
            continue
        data = result.get("data", {})
        return (
            [data["id"]]
            if "id" in data
            else [order["id"] for order in data.get("orders", [])]
        )
    return []


def resolve_targets(run: RunState, prompt: str) -> List[str]:
    # An explicit list request supersedes any ID mentioned as historical context.
    listing = matches(
        r"\b(list|all|any|open)\b.*\borders?\b|\borders?\b.*\b(all|list)\b", prompt
    )
    if listing:
        return []
    targets = order_ids(prompt)
    refers_back = matches(
        r"\b(it|its|that|those|again|instead|still)\b|what did i (buy|purchase)", prompt
    )
    if not targets and refers_back:
        users = prior_user_messages(run)
        targets = recent_order_ids(run) or (order_ids(users[-1]) if users else [])
    if "which" in prompt.lower() and "cancelled" in prompt.lower():
        for observation in reversed(run.get("evidence", [])):
            data = observation.get("result", {}).get("data", {})
            if "orders" in data:
                return [
                    order["id"]
                    for order in data["orders"]
                    if order["status"] == "cancelled"
                ]
    return targets


def wants_policy(run: RunState, prompt: str) -> bool:
    if matches(
        r"\b(warrant\w*|polic\w*|returns?|shipping)\b|can (?:be |i )?cancel|do instead",
        prompt,
    ):
        return True
    # "And my lamp?" carries forward a recent warranty question.
    return matches(r"\b(and|what about)\b", prompt) and any(
        "warranty" in text.lower() for text in prior_user_messages(run)[-4:]
    )


def requests_write(prompt: str) -> bool:
    if matches(r"\b(do not|don.t|never)\s+cancel\b", prompt):
        return False
    return matches(
        r"^(?:please\s+|can you\s+|could you\s+|i want (?:you )?to\s+)?cancel\b", prompt
    ) or matches(r"\b(and|then)\s+cancel\b|ignore.*\bcancel\b", prompt)


def required_operations(
    run: RunState, prompt: str, targets: List[str], write: bool, policy: bool
) -> List[RequiredOperation]:
    order_question = bool(order_ids(prompt)) or matches(
        r"\b(orders?|bought|buy|shipped|tracking|arrive|current status|cancel\w*)\b",
        prompt,
    )
    if (
        matches(r"\b(it|its|that|those|again|instead|still)\b", prompt)
        and recent_order_ids(run)
        and not policy
    ):
        order_question = True
    operations = []
    if order_question and not write:
        operations = [("orders__get_order", {"order_id": target}) for target in targets]
        if not operations:
            # "My orders" means all owned orders, not only currently open ones.
            list_filter = (
                "open" if matches(r"\b(open|active|processing)\b", prompt) else "all"
            )
            if matches(r"\b(all|including)\b", prompt):
                list_filter = "all"
            operations = [("orders__list_orders", {"status": list_filter})]
    if write and targets:
        operations = [("orders__cancel_order", {"order_id": targets[0]})]
    if policy:
        operations.append(("documents__search_documents", {"q": prompt[:200]}))
    if (
        policy
        and not order_ids(prompt)
        and not matches(r"\b(status|my orders|open orders)\b", prompt)
    ):
        query = (
            prompt
            if matches(r"warrant|polic|return|shipping|cancel", prompt)
            else "warranty " + prompt
        )
        operations = [("documents__search_documents", {"q": query[:200]})]
    return operations


def interpret(run: RunState) -> RequestContract:
    prompt = run.get("unresolved_request") or run["prompt"]
    if run.get("unresolved_request"):
        prompt += "\nUser clarification: " + run["prompt"]
    targets = resolve_targets(run, prompt)
    policy, write = wants_policy(run, prompt), requests_write(prompt)
    return RequestContract(
        prompt=prompt,
        targets=targets,
        write=write,
        policy=policy,
        ids_only=matches(r"only.*\b(ids|identifiers)\b", prompt),
        required=required_operations(run, prompt, targets, write, policy),
    )


# These answers describe known API capabilities, not facts about a customer's orders.
CAPABILITY_ANSWERS = [
    (
        r"\b(undo|reverse|reinstate|reactivate)\b",
        "This appliance cannot undo a cancellation or reinstate an order. No change was made.",
    ),
    (
        r"\b(create|place|buy)\b.*\b(new order|a desk|an order)\b|\bchange\b.*\baddress\b",
        "This appliance cannot create orders or change shipping addresses. It supports order lookup, listing, cancellation with approval, and policy lookup. No change was made.",
    ),
    (
        r"\b(help me|can you do|capabilities)\b",
        "I can list your orders, check status and recorded cancellation reasons, look up shipping/returns/warranty policies, and propose a cancellation for your approval. Tell me what you want to do; I can ask for missing details.",
    ),
    (
        r"\b(another customer|belong to alice|alice.s orders)\b",
        "I can only access orders belonging to your signed-in account. A not-found response does not establish whether an order belongs to someone else.",
    ),
    (
        r"\bwho\b.*\bcancel|\bwhat time\b",
        "The order API exposes the recorded cancellation reason, but not who cancelled it or the cancellation time. I cannot determine those details from this API.",
    ),
]


def immediate_reply(
    contract: RequestContract, user: Identity
) -> Optional[Tuple[RunStatus, str]]:
    prompt, targets = contract["prompt"], contract["targets"]
    for pattern, answer in CAPABILITY_ANSWERS:
        if matches(pattern, prompt):
            return "complete", answer
    if "../" in prompt or (
        matches(r"\border\s+\d+\b", prompt) and not order_ids(prompt)
    ):
        return (
            "needs_input",
            "Please confirm the full order ID, such as O-300. Which order do you mean?",
        )
    if contract["write"] and "orders:write" not in user["scopes"]:
        return (
            "complete",
            "Your current identity has read-only order access. I cannot cancel orders with these permissions. No change was made.",
        )
    if contract["write"] and len(targets) != 1:
        return (
            "needs_input",
            "Which order ID would you like to cancel? Please provide one ID, such as O-200.",
        )
    if matches(r"\bwhere is my order\b", prompt) and not targets:
        return (
            "needs_input",
            "Which order would you like me to check? Please give its order ID, or ask me to list all your orders.",
        )
    return None
