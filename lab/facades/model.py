import json
from .. import models
from ..registry import schema_bytes
from .verification import missing_evidence


class ModelGateway:
    """Budgeted and audited model boundary. Provider implementation is in models.py."""

    def __init__(self, guards, audit):
        self.guards, self.audit = guards, audit

    async def ask(self, run, messages, tools, utility=False, max_output=700):
        if not utility and run.get("contract"):
            messages = messages + [
                {
                    "role": "user",
                    "content": "CURRENT REQUEST (answer this, not prior turns): "
                    + run["contract"]["prompt"]
                    + "\nRequired evidence: "
                    + json.dumps(run["contract"]["required"])
                    + "\nWrite authorized: "
                    + str(run["contract"]["write"])
                    + "; allowed order IDs: "
                    + json.dumps(run["contract"]["targets"])
                    + "\nIf information is insufficient use ask_user. If all required API observations are present, answer every part concisely.",
                }
            ]
        allow_final = utility or not missing_evidence(run)
        size = self.guards.before_model(run, messages, tools)
        wire_request = None
        if run["mode"] != "mock":
            endpoint, body = models.build_request(
                messages, tools, max_output, utility, allow_final
            )
            wire_request = {"endpoint": endpoint, "body": body}
            size = len(json.dumps(body).encode())
            if size > self.guards.max_request_bytes:
                raise RuntimeError(
                    "Serialized provider request exceeded the byte guard"
                )
        run["calls"] += 1
        self.audit.record(
            run,
            "utility" if utility else "model",
            "Utility model request" if utility else "Main model request",
            {
                "model": (
                    "scripted mock"
                    if run["mode"] == "mock"
                    else models.UTILITY_MODEL if utility else models.MODEL
                ),
                "messages": messages,
                "tools": tools,
                "request_bytes": size,
                "tool_schema_bytes": schema_bytes(tools),
                "call": run["calls"],
                "protocol": models.PROTOCOL,
                "decision_format": models.DECISION_FORMAT,
                "wire_request": wire_request,
            },
        )
        message, usage = await models.completion(
            run["mode"], messages, tools, max_output, utility, allow_final=allow_final
        )
        raw = usage.pop("_raw_response", None)
        for key in ("input_tokens", "output_tokens"):
            run["usage"][key] += usage.get(key) or 0
        if usage["source"] == "unavailable":
            run["usage"]["missing_usage_calls"] += 1
        self.audit.record(
            run,
            "model_output",
            "Model response",
            {"message": message, "usage": usage, "raw_provider_response": raw},
        )
        return message


class Classifier:
    """Optional cheap semantic hint; never an authorization decision."""

    def __init__(self, model, audit):
        self.model, self.audit = model, audit

    async def classify(self, run):
        if not run["utility"]:
            return
        result = await self.model.ask(
            run,
            [
                {
                    "role": "system",
                    "content": "Classify the user's topic. Return ONLY orders, documents, or general. This is a hint, never permission.",
                },
                {"role": "user", "content": run["prompt"]},
            ],
            [],
            utility=True,
            max_output=20,
        )
        hint = (result.get("content") or "").strip().lower()
        self.audit.record(
            run,
            "classification",
            "Utility classification (advisory only)",
            {"hint": hint if hint in ("orders", "documents", "general") else "unknown"},
        )
