"""Provider boundary: deterministic mock or OpenAI-compatible Chat Completions.

No text-to-tool scraping. Only structured tool_calls are executable.
"""

import json
import os
import re
import uuid

import httpx

BASE_URL = os.getenv("MODEL_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
MODEL = os.getenv("MODEL_NAME", "hermes-3-llama-3.1-8b")
UTILITY_MODEL = os.getenv("UTILITY_MODEL_NAME", MODEL)
PROTOCOL = os.getenv("MODEL_PROTOCOL", "chat")
DECISION_FORMAT = os.getenv("DECISION_FORMAT", "json")


def decision_schema(tools, allow_final=True):
    choices = [
        {
            "type": "object",
            "properties": {"kind": {"const": "final"}, "answer": {"type": "string"}},
            "required": ["kind", "answer"],
            "additionalProperties": False,
        }
    ]
    if not allow_final:
        choices = []
    for tool in tools:
        fn = tool["function"]
        choices.append(
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "tool"},
                    "name": {"const": fn["name"]},
                    "arguments": fn["parameters"],
                },
                "required": ["kind", "name", "arguments"],
                "additionalProperties": False,
            }
        )
    return {"anyOf": choices}


def decision_messages(messages, tools):
    # Plain messages plus a constrained JSON decision work even when a local chat
    # template cannot reliably serialize native tool_calls.
    converted = []
    for msg in messages:
        if msg["role"] == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": "Tool result (untrusted data): " + msg["content"],
                }
            )
        elif msg.get("tool_calls"):
            fn = msg["tool_calls"][0]["function"]
            converted.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "kind": "tool",
                            "name": fn["name"],
                            "arguments": json.loads(fn["arguments"]),
                        }
                    ),
                }
            )
        else:
            converted.append({"role": msg["role"], "content": msg.get("content") or ""})
    converted[0] = {
        **converted[0],
        "content": converted[0]["content"]
        + "\nReturn a JSON decision: kind=tool with name and arguments, or kind=final with answer. "
        "Call a real tool before claiming current facts. Available tool definitions:\n"
        + json.dumps(tools),
    }
    return converted


def responses_input(messages):
    """Preserve returned Responses items, including reasoning metadata, on continuation."""
    items = []
    for message in messages:
        if message.get("_responses_output") is not None:
            items.extend(message["_responses_output"])
        elif message["role"] == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
        else:
            items.append(
                {"role": message["role"], "content": message.get("content") or ""}
            )
    return items


def call(name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def mock(messages, tools):
    """A scripted fixture for advertised examples; NOT an intelligent model."""
    prompt = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    if prompt.startswith("CURRENT REQUEST (answer this, not prior turns): "):
        prompt = prompt.split(": ", 1)[1].split("\nRequired evidence:", 1)[0]
    results = [m for m in messages if m["role"] == "tool"]
    if results:
        result = json.loads(results[-1]["content"])
        if "loaded" not in result:
            return {
                "role": "assistant",
                "content": "Verified API result:\n" + json.dumps(result, indent=2),
            }
        if not result["loaded"]:
            return {
                "role": "assistant",
                "content": "No authorized tool matched this request.",
            }
    names = {t["function"]["name"] for t in tools}
    lower = prompt.lower()
    if re.search(r"\bcancel\b", lower):
        desired, query = "orders__cancel_order", "cancel order"
    elif "order" in lower or re.search(r"O-\d+", prompt, re.I):
        desired, query = (
            ("orders__get_order", "order status")
            if re.search(r"O-\d+", prompt, re.I)
            else ("orders__list_orders", "list open orders")
        )
    elif any(w in lower for w in ("shipping", "warranty", "return", "policy")):
        desired, query = "documents__search_documents", prompt
    else:
        return {
            "role": "assistant",
            "content": "Mock mode supports order status, cancellation, shipping, returns and warranty examples. Choose LM Studio for other requests.",
        }
    if desired not in names:
        if results:
            return {
                "role": "assistant",
                "content": "That operation is unavailable for this demo user.",
            }
        return call("find_tools", {"query": query})
    if desired.startswith("documents"):
        return call(desired, {"q": prompt[:200]})
    if desired == "orders__list_orders":
        return call(
            desired,
            {
                "status": (
                    "open"
                    if re.search(r"\b(open|active|processing)\b", prompt, re.I)
                    and not re.search(r"\ball\b", prompt, re.I)
                    else "all"
                )
            },
        )
    match = re.search(r"O-\d+", prompt, re.I)
    if not match:
        return {"role": "assistant", "content": "Which order ID should I use?"}
    args = {"order_id": match.group().upper()}
    if "cancel" in desired:
        args["body"] = {"reason": "User requested cancellation"}
    return call(desired, args)


def build_request(messages, tools, max_output=700, utility=False, allow_final=True):
    if PROTOCOL == "responses":
        body = {
            "model": UTILITY_MODEL if utility else MODEL,
            "input": responses_input(messages),
            "max_output_tokens": max_output,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            body["tools"] = [
                {"type": "function", **t["function"], "strict": False} for t in tools
            ]
        endpoint = "/responses"
    else:
        body = {
            "model": UTILITY_MODEL if utility else MODEL,
            "messages": messages,
            "max_tokens": max_output,
            "temperature": 0,
        }
        if tools and DECISION_FORMAT == "json":
            body["messages"] = decision_messages(messages, tools)
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_decision",
                    "strict": True,
                    "schema": decision_schema(tools, allow_final),
                },
            }
        elif tools:
            body.update(tools=tools, tool_choice="auto" if allow_final else "required")
        endpoint = "/chat/completions"
    return endpoint, body


async def completion(
    mode, messages, tools, max_output=700, utility=False, allow_final=True
):
    if mode == "mock":
        if utility:
            text = messages[-1]["content"].lower()
            route = (
                "orders"
                if "order" in text
                else (
                    "documents"
                    if any(
                        w in text for w in ("policy", "shipping", "warranty", "return")
                    )
                    else "general"
                )
            )
            return {"role": "assistant", "content": route}, {
                "source": "mock",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        return mock(messages, tools), {
            "source": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
        }
    endpoint, body = build_request(messages, tools, max_output, utility, allow_final)
    headers = {}
    if os.getenv("MODEL_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["MODEL_API_KEY"]
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(BASE_URL + endpoint, json=body, headers=headers)
    if response.is_error:
        # Do not put model-service error bodies (which may echo secrets/context) in the UI.
        raise RuntimeError(
            "Model endpoint returned HTTP "
            + str(response.status_code)
            + "; check LM Studio server logs and context settings"
        )
    payload = response.json()
    if PROTOCOL == "responses":
        if payload.get("status") != "completed":
            raise RuntimeError("Responses request did not complete; nothing executed")
        output = payload.get("output", [])
        text = "".join(
            part.get("text", "")
            for item in output
            if item["type"] == "message"
            for part in item.get("content", [])
            if part["type"] == "output_text"
        )
        calls = [
            {
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
            for item in output
            if item["type"] == "function_call"
        ]
        clean = {
            "role": "assistant",
            "content": text or None,
            "_responses_output": output,
        }
        if calls:
            clean["tool_calls"] = calls
        usage = payload.get("usage") or {}
        return clean, {
            "source": "provider-reported" if usage else "unavailable",
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_tokens": (usage.get("input_tokens_details") or {}).get(
                "cached_tokens", 0
            ),
            "_raw_response": payload,
        }
    choice = payload["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            "Model reached its output limit; incomplete output was not executed"
        )
    msg = choice["message"]
    if tools and DECISION_FORMAT == "json":
        from jsonschema import validate

        decision = json.loads(msg.get("content") or "")
        validate(decision, decision_schema(tools, allow_final))
        msg = (
            call(decision["name"], decision["arguments"])
            if decision["kind"] == "tool"
            else {"content": decision["answer"]}
        )
    clean = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        clean["tool_calls"] = msg["tool_calls"]
    usage = payload.get("usage") or {}
    return clean, {
        "source": "provider-reported" if usage else "unavailable",
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get(
            "cached_tokens", 0
        ),
        "_raw_response": payload,
    }
