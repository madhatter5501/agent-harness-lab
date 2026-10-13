import copy
import json

import httpx

from ..common import decode_token
from ..registry import SEARCH
from .dialogue import ASK_USER
from .verification import missing_evidence


class ToolGateway:
    """Discovery and canonical HTTP execution. Knows transport, not business rules."""

    def __init__(self, catalog, guards, audit):
        self.catalog, self.guards, self.audit = catalog, guards, audit

    def definitions(self, run):
        missing = missing_evidence(run)
        if missing:
            needed = {name for name, args in missing}
            loaded = []
            for name in run["loaded"]:
                if name not in needed:
                    continue
                definition = copy.deepcopy(self.catalog.tools[name].definition())
                properties = definition["function"]["parameters"]["properties"]
                for key in ("order_id", "status", "q"):
                    allowed = [
                        args[key]
                        for operation, args in missing
                        if operation == name and key in args
                    ]
                    if key in properties and allowed:
                        properties[key]["enum"] = list(dict.fromkeys(allowed))
                loaded.append(definition)
            return (
                ([SEARCH] if needed - set(run["loaded"]) else []) + [ASK_USER] + loaded
            )
        return [SEARCH, ASK_USER] + [
            self.catalog.tools[name].definition() for name in run["loaded"]
        ]

    def discover(self, run, decision, user):
        self.guards.discovery(decision)
        args = decision["args"]
        selected = self.catalog.search(args["query"], args.get("service", "any"), user)
        for tool in selected:
            if tool.name not in run["loaded"]:
                run["loaded"].append(tool.name)
        self.audit.record(
            run,
            "discovery",
            "Registry search; schemas loaded for next call",
            {"predicate": args, "loaded": [t.definition() for t in selected]},
        )
        return {
            "loaded": [
                {"name": t.name, "description": t.description} for t in selected
            ],
            "instruction": "Definitions are now available. Call the matching tool next.",
        }

    def validate(self, run, decision, user):
        tool = self.catalog.tools.get(decision["name"])
        if tool is None:
            raise RuntimeError("Unknown tool")
        self.guards.before_tool(run, tool, decision, user)
        self.audit.record(
            run,
            "guard",
            "Tool schema and scope checks passed",
            {"name": tool.name, "args": decision["args"], "effect": tool.effect},
        )
        return tool

    async def execute(self, run, decision, token):
        # Revalidate on resume: approval is not a substitute for current authorization.
        user = decode_token(token)
        tool = self.validate(run, decision, user)
        request = tool.request(decision["args"])
        key = run["id"] + ":" + decision["id"]
        self.audit.record(
            run,
            "http",
            "Calling canonical " + tool.service + " API",
            {
                **request,
                "idempotency_key": key,
                "actor": user["sub"],
                "credentials": "[not logged]",
            },
        )
        async with httpx.AsyncClient(timeout=10) as client:
            async with client.stream(
                **request,
                headers={"Authorization": "Bearer " + token, "Idempotency-Key": key}
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 32000:
                        raise RuntimeError(
                            "Tool result exceeded 32KB; narrow the request"
                        )
        try:
            result = json.loads(data)
        except ValueError:
            raise RuntimeError("API returned non-JSON data")
        self.audit.record(
            run,
            "tool_result",
            "Canonical API response",
            {"http_status": response.status_code, "body": result},
        )
        return {"http_status": response.status_code, "data": result}


class Approvals:
    """Persist exact proposed actions. The UI can approve or decline, not alter arguments."""

    def __init__(self, audit):
        self.audit = audit

    def pause_if_needed(self, run, tool, decision):
        if tool.effect != "write":
            return False
        run["pending"] = {
            "kind": "tool",
            **{k: decision[k] for k in ("id", "name", "args")},
        }
        run["status"] = "approval_required"
        self.audit.record(
            run,
            "approval",
            "Paused before write: approve exact arguments",
            run["pending"],
        )
        return True

    def decide(self, run, approved):
        if run["status"] != "approval_required":
            raise ValueError("This run has no pending approval")
        self.audit.record(
            run,
            "approval",
            "User approved exact action" if approved else "User declined action",
            run["pending"],
        )
        run["status"] = "running" if approved else "denied"
        if not approved:
            run["answer"] = "Action declined. No cancellation request was sent."
