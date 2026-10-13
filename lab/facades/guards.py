import json

from jsonschema import validate

from ..registry import SEARCH
from .dialogue import ASK_USER
from ..errors import NeedsClarification
from ..state import Decision, Identity, RunState


class Guardrails:
    """Deterministic boundaries. Models can propose; these checks decide what can run."""

    max_calls = 8
    max_request_bytes = 24000

    def admit(self, run: RunState, user: Identity) -> None:
        if not 0 < len(run["prompt"]) <= 3000:
            raise ValueError("Request must contain 1–3000 characters")
        if run["owner"] != user["sub"] or run["tenant"] != user["tenant"]:
            raise ValueError("Execution identity mismatch")

    def before_model(self, run, messages, tools):
        if run["calls"] >= self.max_calls:
            raise RuntimeError("Model-call budget exhausted")
        size = len(json.dumps({"messages": messages, "tools": tools}).encode())
        if size > self.max_request_bytes:
            raise RuntimeError(
                "Working context exceeded the byte guard. Shorten the request; the audit remains intact."
            )
        return size

    def decision(self, run: RunState, message: dict) -> Decision:
        calls = message.get("tool_calls") or []
        if not calls:
            return {"kind": "final", "answer": message.get("content") or ""}
        if len(calls) != 1:
            raise RuntimeError(
                "This teaching loop accepts exactly one tool call per decision"
            )
        call = calls[0]
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        if name == "ask_user":
            validate(args, ASK_USER["function"]["parameters"])
            return {"kind": "clarify", "question": args["question"]}
        signature = json.dumps([name, args], sort_keys=True)
        if signature in run["seen"]:
            raise RuntimeError("Repeated identical tool call blocked: no progress")
        run["seen"].append(signature)
        return {
            "kind": "discovery" if name == "find_tools" else "tool",
            "id": call["id"],
            "name": name,
            "args": args,
        }

    def discovery(self, decision):
        validate(decision["args"], SEARCH["function"]["parameters"])

    def before_tool(self, run, tool, decision, user):
        if tool.name not in run["loaded"]:
            raise RuntimeError("Undiscovered tool cannot execute")
        if tool.scope not in user["scopes"]:
            raise RuntimeError("Tool permission denied")
        tool.validate(decision["args"])
        contract = run.get("contract")
        if contract:
            if tool.effect == "write" and not contract["write"]:
                raise RuntimeError(
                    "Write blocked: the current request does not authorize this action. No write was sent."
                )
            target = decision["args"].get("order_id")
            if target and target not in contract["targets"]:
                raise NeedsClarification(
                    "Which order do you mean? Please confirm the full order ID. No action has been taken."
                )
            if tool.effect == "write":
                reason = decision["args"].get("body", {}).get("reason", "")
                if (
                    reason != "User requested cancellation"
                    and reason.lower() not in contract["prompt"].lower()
                ):
                    raise NeedsClarification(
                        f"What reason should I record for cancelling {target}? No cancellation has been sent."
                    )
