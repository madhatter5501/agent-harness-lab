"""Serializable types shared by the loop, API, audit log, and tests.

TypedDict keeps JSON persistence straightforward while making fields and decision
variants visible to an editor. HTTP inputs are validated separately by Pydantic.
"""

from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union
from uuid import uuid4

RunStatus = Literal[
    "running",
    "needs_input",
    "approval_required",
    "complete",
    "denied",
    "failed",
    "interrupted",
]
Mode = Literal["mock", "lmstudio"]
DiscoveryMode = Literal["lazy", "eager"]
RequiredOperation = Tuple[str, Dict[str, Any]]
FOLLOW_UP_STATUSES = {"needs_input", "complete", "denied", "failed", "interrupted"}


class Identity(TypedDict):
    sub: str
    tenant: str
    scopes: List[str]


class RequestContract(TypedDict):
    prompt: str
    targets: List[str]
    write: bool
    policy: bool
    ids_only: bool
    required: List[RequiredOperation]


class FinalDecision(TypedDict):
    kind: Literal["final"]
    answer: str


class ClarifyDecision(TypedDict):
    kind: Literal["clarify"]
    question: str


class ToolDecision(TypedDict):
    kind: Literal["tool", "discovery"]
    id: str
    name: str
    args: Dict[str, Any]


Decision = Union[FinalDecision, ClarifyDecision, ToolDecision]


class RunState(TypedDict, total=False):
    id: str
    owner: str
    tenant: str
    status: RunStatus
    prompt: str
    mode: Mode
    discovery: DiscoveryMode
    utility: bool
    previous_run: Optional[str]
    unresolved_request: Optional[str]
    task_prompt: str
    contract: RequestContract
    events: List[Dict[str, Any]]
    messages: List[Dict[str, Any]]
    loaded: List[str]
    calls: int
    summary: str
    conversation: List[Dict[str, Any]]
    evidence: List[Dict[str, Any]]
    pending: Optional[ToolDecision]
    usage: Dict[str, int]
    answer: str
    seen: List[str]
    output_repairs: int


def create_run(
    prompt: str,
    user: Identity,
    *,
    mode: Mode = "lmstudio",
    discovery: DiscoveryMode = "lazy",
    utility: bool = False,
    previous: Optional[RunState] = None
) -> RunState:
    """The only place new runs are constructed; never share mutable parent state."""
    if previous:
        if previous["owner"] != user["sub"] or previous["tenant"] != user["tenant"]:
            raise ValueError("Conversation identity mismatch")
        if previous["status"] not in FOLLOW_UP_STATUSES:
            raise ValueError("Finish the previous run before a follow-up")
    prior = previous or {}
    return RunState(
        id=uuid4().hex,
        owner=user["sub"],
        tenant=user["tenant"],
        status="running",
        prompt=prompt,
        mode=mode,
        discovery=discovery,
        utility=utility,
        previous_run=prior.get("id"),
        unresolved_request=(
            prior.get("unresolved_request")
            if prior.get("status") == "needs_input"
            else None
        ),
        events=[],
        messages=[],
        loaded=[],
        calls=0,
        pending=None,
        answer="",
        seen=[],
        output_repairs=0,
        summary=prior.get("summary", ""),
        conversation=deepcopy(prior.get("conversation", [])),
        evidence=deepcopy(prior.get("evidence", [])[-12:]),
        usage={"input_tokens": 0, "output_tokens": 0, "missing_usage_calls": 0},
    )
