"""Composition root: construct components here; AgentLoop knows only their interfaces."""

from .facades.dialogue import Dialogue
from .facades.guards import Guardrails
from .facades.memory import WorkingMemory
from .facades.model import Classifier, ModelGateway
from .facades.persistence import AuditLog, RunStore
from .facades.tools import Approvals, ToolGateway
from .facades.recovery import Recovery
from .facades.verification import ResponseVerifier
from .loop import AgentLoop


def build_runtime(catalog):
    store = RunStore()
    audit = AuditLog(store)
    guards = Guardrails()
    model = ModelGateway(guards, audit)
    return AgentLoop(
        guards=guards,
        dialogue=Dialogue(audit),
        verifier=ResponseVerifier(audit),
        recovery=Recovery(audit),
        classifier=Classifier(model, audit),
        memory=WorkingMemory(model, audit),
        model=model,
        tools=ToolGateway(catalog, guards, audit),
        approvals=Approvals(audit),
        audit=audit,
        store=store,
    )
