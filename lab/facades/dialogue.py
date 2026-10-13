"""Conversation and demo-domain request contract, separate from the agent loop.

These deterministic rules describe this small appliance's capabilities. They are
not a general natural-language intent classifier or a security identity source.
"""

from ..policies.order_requests import interpret, immediate_reply
from ..state import Identity, RunState, RunStatus

ASK_USER = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "Ask the user for missing information or clarify an ambiguous goal. Do not guess IDs, values, or intent.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 5, "maxLength": 500}
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}


class Dialogue:
    """Conversation lifecycle. Demo vocabulary lives in policies/order_requests.py."""

    def __init__(self, audit):
        self.audit = audit

    def prepare(self, run: RunState, user: Identity):
        run["contract"] = interpret(run)
        run["task_prompt"] = run["contract"]["prompt"]
        self.audit.record(
            run,
            "contract",
            "Current request and evidence requirements",
            run["contract"],
        )
        return immediate_reply(run["contract"], user)

    def finish(self, run: RunState, status: RunStatus, answer: str, memory) -> None:
        run["status"], run["answer"] = status, answer
        if status == "needs_input":
            run["unresolved_request"] = run.get("task_prompt", run["prompt"])
        else:
            run["unresolved_request"] = None
        memory.finish(run)
        self.audit.record(
            run,
            "clarification" if status == "needs_input" else "complete",
            "Waiting for your reply" if status == "needs_input" else "Response ready",
            {"answer": answer},
        )
