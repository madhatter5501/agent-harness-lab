"""Compile a deliberately small OpenAPI subset into callable HTTP tools.

This is a readable adapter, not a general-purpose OpenAPI implementation. Unsupported
operations fail startup instead of guessing at serialization. FastMCP's OpenAPI
provider is a production alternative when you need wider protocol coverage.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from jsonschema import Draft202012Validator

SEARCH = {
    "type": "function",
    "function": {
        "name": "find_tools",
        "description": "Find and load API tools. Search orders/status/cancellation or documents/shipping/returns/warranty. Returns callable tools for your next turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 200},
                "service": {"type": "string", "enum": ["orders", "documents", "any"]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
CAPABILITIES = "orders: list current/open orders without an ID, look up status by ID, and cancellation. documents: shipping, returns and warranty policies."


def resolve(value, spec, depth=0):
    if depth > 15:
        raise ValueError("Recursive schemas are not supported in this teaching adapter")
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/"):
                raise ValueError("Only local OpenAPI references are supported")
            target = spec
            for key in ref[2:].split("/"):
                target = target[key.replace("~1", "/").replace("~0", "~")]
            return resolve(target, spec, depth + 1)
        return {k: resolve(v, spec, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, spec, depth + 1) for v in value]
    return value


@dataclass
class Tool:
    service: str
    base_url: str
    path: str
    method: str
    operation: str
    description: str
    schema: dict
    params: list
    scope: str
    effect: str

    @property
    def name(self):
        return self.service + "__" + self.operation

    def definition(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def validate(self, args):
        errors = sorted(
            Draft202012Validator(self.schema).iter_errors(args),
            key=lambda e: str(e.path),
        )
        if errors:
            raise ValueError("; ".join(e.message for e in errors)[:500])

    def request(self, args):
        self.validate(args)
        path, query = self.path, {}
        for param in self.params:
            name = param["name"]
            if name not in args:
                continue
            if param["in"] == "path":
                value = str(args[name])
                if "/" in value or value in (".", ".."):
                    raise ValueError("Invalid path parameter")
                path = path.replace("{" + name + "}", quote(value, safe=""))
            elif param["in"] == "query":
                query[name] = args[name]
        return {
            "method": self.method,
            "url": self.base_url + path,
            "params": query,
            "json": args.get("body"),
        }


class Registry:
    def __init__(self):
        self.tools = {}

    def add(self, service, url, spec):
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                meta = operation.get("x-agent") if isinstance(operation, dict) else None
                if not meta:
                    continue
                if method not in ("get", "post"):
                    raise ValueError("Teaching adapter supports GET/POST only")
                if methods.get("parameters"):
                    raise ValueError("Path-level parameters are not supported")
                params = resolve(operation.get("parameters", []), spec)
                props, required = {}, []
                for param in params:
                    if (
                        param["in"] == "header"
                        and param["name"].lower() == "idempotency-key"
                    ):
                        continue  # Trusted code injects this, never the model.
                    if param["in"] not in ("path", "query") or param["schema"].get(
                        "type"
                    ) not in ("string", "integer", "number", "boolean"):
                        raise ValueError("Unsupported parameter serialization")
                    props[param["name"]] = param["schema"]
                    if param.get("required"):
                        required.append(param["name"])
                body = operation.get("requestBody")
                if body:
                    if set(body["content"]) != {"application/json"}:
                        raise ValueError("Only JSON request bodies are supported")
                    props["body"] = resolve(
                        body["content"]["application/json"]["schema"], spec
                    )
                    props["body"]["additionalProperties"] = False
                    if body.get("required"):
                        required.append("body")
                schema = {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                }
                tool = Tool(
                    service,
                    url.rstrip("/"),
                    path,
                    method.upper(),
                    operation["operationId"],
                    operation.get("summary", ""),
                    schema,
                    params,
                    meta["scope"],
                    meta["effect"],
                )
                if tool.name in self.tools:
                    raise ValueError("Duplicate tool name")
                self.tools[tool.name] = tool

    def allowed(self, user):
        return [t for t in self.tools.values() if t.scope in user["scopes"]]

    def search(self, query, service, user):
        words = set(re.findall(r"[a-z]+", query.lower()))
        candidates = [
            t for t in self.allowed(user) if service == "any" or t.service == service
        ]

        def score(t):
            text = (t.name + " " + t.description).lower()
            return sum(w in text for w in words)

        return [t for t in sorted(candidates, key=score, reverse=True) if score(t) > 0][
            :3
        ]


async def load_registry(services):
    registry = Registry()
    async with httpx.AsyncClient(timeout=10) as client:
        for name, url in services.items():
            response = await client.get(url + "/openapi.json")
            response.raise_for_status()
            registry.add(name, url, response.json())
    return registry


def schema_bytes(definitions):
    return len(json.dumps(definitions, separators=(",", ":")).encode())
