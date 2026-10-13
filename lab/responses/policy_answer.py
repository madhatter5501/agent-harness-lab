"""Presentation only: every factual value comes from an API observation."""

import re


def policy_answer(data: dict, text: str, order_items: list) -> list:
    lines = []
    matches = data.get("matches", [])
    if not matches:
        lines.append(
            "I couldn't find a policy covering that topic, so I can't confirm an answer from the available documents."
        )
    for article in matches:
        content = article["text"]
        if article["id"] == "warranty":
            item_text = text + " " + " ".join(order_items).lower()
            lamp = bool(re.search(r"\blamps?\b", item_text))
            furniture = bool(re.search(r"\b(desks?|chairs?)\b", item_text))
            # A desk lamp is a lamp, not a desk.
            if "desk lamp" in item_text and not any(
                "standing desk" in x.lower() for x in order_items
            ):
                furniture = False
            sentences = re.split(r"(?<=\.)\s+", content)
            if lamp and not furniture:
                content = next((s for s in sentences if s.startswith("Lamps")), content)
            elif furniture and not lamp:
                content = next((s for s in sentences if s.startswith("Desks")), content)
        if article["id"] == "returns":
            requested_days = re.search(r"after (\d+) days", text)
            policy_days = re.search(r"within (\d+) days", content)
            if (
                requested_days
                and policy_days
                and int(requested_days[1]) > int(policy_days[1])
            ):
                content = (
                    f"No—the recorded return window is {policy_days[1]} days after delivery. "
                    + content
                )
        lines.append(content)
    return lines
