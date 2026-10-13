"""Presentation only: every factual value comes from an API observation."""

import re


def list_orders(data: dict, contract: dict, text: str) -> list:
    lines = []
    orders = data.get("orders", [])
    if contract.get("ids_only"):
        lines.append(", ".join(o["id"] for o in orders) or "No matching order IDs.")
    elif not orders:
        lines.append(
            "You don't have any open orders."
            if data.get("filter") == "open"
            else "I found no orders belonging to your account."
        )
    else:
        label = (
            "Your open orders are"
            if data.get("filter") == "open"
            else "Your orders are"
        )
        if "no orders" in text:
            label = "No—you do have orders"
        lines.append(
            label
            + ": "
            + "; ".join(
                f"{o['id']} ({o['item'].lower()}, {o['status']})" for o in orders
            )
            + "."
        )
    if data.get("has_more"):
        lines.append("This shows the first 100 matches; more orders exist.")
    return lines
