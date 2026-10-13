"""START HERE: one visible loop; all policy and transport live behind facades.

admit -> interpret -> prepare -> decide -> clarify / answer / discover / act
An action must pass validation and, for writes, approval before execution.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .errors import InvalidAnswer, NeedsClarification
from .facades.dialogue import Dialogue
from .facades.guards import Guardrails
from .facades.memory import WorkingMemory
from .facades.model import Classifier, ModelGateway
from .facades.persistence import AuditLog, RunStore
from .facades.recovery import Recovery
from .facades.tools import Approvals, ToolGateway
from .facades.verification import ResponseVerifier
from .state import Identity, RunState, RunStatus, ToolDecision


@dataclass
class AgentLoop:
    guards: Guardrails
    classifier: Classifier
    memory: WorkingMemory
    model: ModelGateway
    tools: ToolGateway
    approvals: Approvals
    audit: AuditLog
    store: RunStore
    dialogue: Dialogue
    verifier: ResponseVerifier
    recovery: Recovery

    async def run(
        self, state: RunState, user: Identity, token: str, approved: bool = False
    ) -> None:
        """Advance one turn until an answer, clarification, approval, or failure."""
        try:
            self.guards.admit(state, user)
            self.audit.record(
                state,
                "guard",
                "Identity and request checks passed",
                {
                    "actor": user["sub"],
                    "tenant": user["tenant"],
                    "scopes": user["scopes"],
                    "max_model_calls": self.guards.max_calls,
                },
            )

            if approved:
                # Only the saved action resumes; current API permissions are checked again.
                pending = state["pending"]
                if pending is None:
                    raise RuntimeError("No saved action is available to resume")
                await self.execute_and_observe(state, pending, token)
            else:
                immediate = self.dialogue.prepare(state, user)
                if immediate:
                    self.dialogue.finish(state, *immediate, self.memory)
                    return
                await self.classifier.classify(state)
                await self.memory.prepare(state, self.tools, user)

            while state["calls"] < self.guards.max_calls:
                message = await self.model.ask(
                    state, state["messages"], self.tools.definitions(state)
                )
                decision = self.guards.decision(state, message)

                if decision["kind"] == "clarify":
                    self.dialogue.finish(
                        state, "needs_input", decision["question"], self.memory
                    )
                    return

                if decision["kind"] == "final":
                    try:
                        answer = self.verifier.check(state, decision["answer"])
                    except InvalidAnswer as exc:
                        self.recovery.request_correction(state, str(exc))
                        continue
                    self.dialogue.finish(state, "complete", answer, self.memory)
                    return

                state["messages"].append(message)
                if decision["kind"] == "discovery":
                    definitions = self.tools.discover(state, decision, user)
                    self.memory.add_result(state, decision["id"], definitions)
                    continue

                tool = self.tools.validate(state, decision, user)
                if self.approvals.pause_if_needed(state, tool, decision):
                    return
                await self.execute_and_observe(state, decision, token)

            raise RuntimeError("Model-call budget exhausted")

        except NeedsClarification as exc:
            self.audit.record(
                state,
                "guard",
                "Ungrounded action paused for clarification",
                {"question": str(exc)},
            )
            self.dialogue.finish(state, "needs_input", str(exc), self.memory)
        except asyncio.CancelledError:
            self.stop(
                state,
                "interrupted",
                "Execution interrupted. Inspect API state before retrying a write.",
            )
            raise
        except Exception as exc:
            message = (
                str(exc)[:600]
                if isinstance(exc, (RuntimeError, ValueError))
                else type(exc).__name__ + ": check service logs/connectivity"
            )
            self.stop(state, "failed", message)

    async def execute_and_observe(
        self, state: RunState, decision: ToolDecision, token: str
    ) -> None:
        result = await self.tools.execute(state, decision, token)
        self.memory.observe(state, decision, result)
        self.store.save(state)

    def stop(self, state: RunState, status: RunStatus, message: str) -> None:
        state["status"], state["answer"] = status, message
        self.audit.record(state, "error", "Run stopped safely", {"message": message})
