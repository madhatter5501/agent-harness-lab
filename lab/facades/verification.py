"""Evidence checks and canonical rendering for this demo's operational answers.

No second LLM judges its own prose. The supported API fields are rendered by
code. Arbitrary conversational answers remain outside this domain verifier.
"""

import re
import json

from ..errors import InvalidAnswer
from ..state import RunState
from ..responses.render import render_evidence


def current_evidence(run):
    return [e for e in run.get("evidence", []) if e["run"] == run["id"]]


def missing_evidence(run):
    observations = current_evidence(run)
    return [
        (name, args)
        for name, args in run.get("contract", {}).get("required", [])
        if not any(
            e["tool"] == name and all(e["args"].get(k) == v for k, v in args.items())
            for e in observations
        )
    ]


class ResponseVerifier:
    """Verify coverage, then produce an answer from authoritative API fields."""

    def __init__(self, audit):
        self.audit = audit

    def check(self, run: RunState, proposed: str) -> str:
        answer = self.answer(run, proposed)
        self.audit.record(
            run,
            "verification",
            "Response checked against current evidence",
            {
                "sources": [
                    {"run": e["run"], "tool": e["tool"], "args": e["args"]}
                    for e in current_evidence(run)
                ],
                "required": run.get("contract", {}).get("required", []),
                "rendered_from_api": bool(run.get("contract", {}).get("required")),
                "proposed_answer": proposed,
                "delivered_answer": answer,
            },
        )
        return answer

    def answer(self, run: RunState, answer: str) -> str:
        tool_text = re.search(
            r"<tool_call>|tool_calls\s*=|['\"](?:arguments|tool_calls)['\"]\s*:|"
            r"(?:please\s+)?call\s*:\s*\{|\b(?:orders|documents)__[a-z_]+\b",
            answer,
            re.I,
        )
        if not answer.strip() or tool_text:
            raise InvalidAnswer(
                "Tool-call text or empty answer was withheld. Issue a structured tool request, not instructions for the user to call it. Never use placeholder arguments."
            )
        missing = missing_evidence(run)
        if missing:
            raise InvalidAnswer(
                "The answer is missing required current API evidence: "
                + json.dumps(missing)
                + ". Call these APIs before answering; do not repeat an unrelated tool."
            )
        if run.get("contract", {}).get("required"):
            return render_evidence(run, answer)
        return answer
