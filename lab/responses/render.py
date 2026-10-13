"""Choose a small presentation function for each verified API result."""

from .list_orders import list_orders
from .order_detail import order_detail
from .policy_answer import policy_answer


def render_evidence(run: dict, proposed: str) -> str:
    contract = run.get("contract", {})
    if not contract.get("required"):
        return proposed
    observations = [e for e in run.get("evidence", []) if e["run"] == run["id"]]
    # Answer the order question before the policy explanation, regardless of call order.
    observations.sort(key=lambda e: e["tool"].startswith("documents__"))
    text = contract.get("prompt", run["prompt"]).lower()
    items = [
        e["result"]["data"].get("item", "")
        for e in observations
        if e["tool"] == "orders__get_order" and e["result"]["http_status"] == 200
    ]
    lines = []
    for observation in observations:
        result, name = observation["result"], observation["tool"]
        data = result.get("data", {})
        if result["http_status"] >= 400:
            target = observation.get("args", {}).get("order_id", "that order")
            lines.append(
                f"I couldn't find {target} in your account."
                if result["http_status"] == 404
                else f"I couldn't complete that request: {data.get('detail', 'the API rejected it')}."
            )
        elif name == "orders__list_orders":
            lines.extend(list_orders(data, contract, text))
        elif name == "orders__get_order":
            lines.extend(order_detail(data, contract, text))
        elif name == "documents__search_documents":
            lines.extend(policy_answer(data, text, items))
        elif name == "orders__cancel_order":
            lines.append(
                f"{data['id']} is {data['status']}. The recorded reason is: {data.get('reason', 'unavailable')}."
            )
    return "\n\n".join(dict.fromkeys(lines)) or proposed
