"""Presentation only: every factual value comes from an API observation."""

import re


def order_detail(data: dict, contract: dict, text: str) -> list:
    lines = []
    target, item, status = data["id"], data["item"].lower(), data["status"]
    if contract.get("ids_only"):
        lines.append(target)
    elif re.search(r"\b(tracking|arrive|arrival|delivery date)\b", text):
        lines.append(
            f"I can't tell you the tracking number or exact arrival date for {target}; this API doesn't provide those details."
        )
    elif re.search(r"what (?:did i buy|item)|what did i purchase", text):
        article = "an" if item[0] in "aeiou" else "a"
        lines.append(f"You ordered {article} {item} ({target}).")
    elif re.search(r"did you.*cancel", text) and status != "cancelled":
        lines.append(f"No. {target} is currently {status}; it is not cancelled.")
    elif "shipped yet" in text:
        lines.append(
            f"{'Yes' if status == 'shipped' else 'No'}—{target} is currently {status}."
        )
    else:
        lines.append(f"{target}, your {item}, is {status}.")
        if status == "cancelled":
            reason = data.get("cancellation_reason")
            lines.append(
                "The recorded cancellation reason is: " + reason
                if reason
                else "No cancellation reason is recorded, so I cannot tell you why."
            )
            if "payment" in text:
                lines.append("The record does not establish a payment failure.")
        elif status == "shipped" and re.search(r"cancel|do instead", text):
            lines.append(
                "It can't be cancelled once shipped. The return policy below explains the available option."
            )
    return lines
