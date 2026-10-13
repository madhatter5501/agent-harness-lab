# Follow one request through the harness

Start with `lab/loop.py`, then `lab/runtime.py`. The loop describes the flow; the runtime constructs and injects each component. You do not need to understand Docker, HTTP serialization, or model protocols to read the loop.

## Example: “Where is my order?” → “O-300”

1. The chat sends the message to `POST /runs` in `lab/harness.py`.
2. `create_run()` in `lab/state.py` creates a fresh run. A follow-up copies conversation context; it does not share mutable lists with its parent.
3. `guards.admit()` checks the input and execution identity.
4. `dialogue.prepare()` calls the demo request interpreter. This request lacks an order ID, so it returns a clarification immediately.
5. `dialogue.finish()` sets `needs_input`, saves the question in conversation memory, and records an audit event. No API action has happened.
6. The user replies “O-300”. The chat sends a new run linked by `previous_run`. The factory carries the unresolved request forward.
7. The interpreter resolves the clarified request into a required operation: `get_order(order_id="O-300")`.
8. The model discovers the tool. `ToolGateway` exposes its schema, narrowed to the unresolved target. While required evidence is missing, JSON decision mode does not offer a final-answer choice; `ask_user` remains available.
9. The model proposes the tool call. Guards check its schema, permission, and target. The canonical orders API checks the authenticated owner and tenant again.
10. Working memory records the API observation. The verifier checks that it matches the required operation, then the presentation layer writes the conversational answer from the returned fields.
11. The answer and audit are saved. The chat displays the exchange and lets the user continue.

## Where to make a change

| Concern | File or directory |
|---|---|
| Overall control flow | `lab/loop.py` |
| Dependency wiring | `lab/runtime.py` |
| State fields, decision variants, run creation | `lab/state.py` |
| HTTP endpoints and background task launch | `lab/harness.py` |
| Conversation completion and clarification | `lab/facades/dialogue.py` |
| Demo vocabulary and request interpretation | `lab/policies/order_requests.py` |
| Identity, execution budget, action validation | `lab/facades/guards.py` |
| Evidence coverage and final-answer validation | `lab/facades/verification.py` |
| Bounded correction attempts | `lab/facades/recovery.py` |
| Conversational phrasing from API fields | `lab/responses/` |
| Model HTTP protocol and scripted mock | `lab/models.py` |
| Provider accounting and optional classifier | `lab/facades/model.py` |
| Working context and summarization | `lab/facades/memory.py` |
| Tool discovery, HTTP execution, approval | `lab/facades/tools.py` |
| OpenAPI-to-tool compilation | `lab/registry.py` |
| Canonical business rules and ownership | `lab/orders.py`, `lab/documents.py` |
| SQLite checkpoints and event history | `lab/facades/persistence.py` |
| Page structure, behavior, appearance | `lab/static/index.html`, `app.js`, `styles.css` |

## The four decisions

The model can request discovery, request an API action, ask the user a question, or propose a final answer. Their shapes are named in `state.py`. `ask_user` is a structured tool-shaped decision, but it pauses the conversation rather than invoking a downstream service.

A clarification asks for information. An approval authorizes an exact proposed write. They are different states and must not be interchangeable.

## Run states

| State | Meaning | Next step |
|---|---|---|
| `running` | The loop is working | Wait |
| `needs_input` | Missing information or an ungrounded proposed value | User replies in chat |
| `approval_required` | An exact write is saved but unexecuted | Approve or decline |
| `complete` | An answer is ready | Optional follow-up |
| `denied` | The user declined the write | No write is sent |
| `failed` | A bounded error stopped this turn | Inspect its audit |
| `interrupted` | Shutdown or restart interrupted work | Inspect state before retrying a write |

`TypedDict` provides editor/type-checker guidance and preserves ordinary JSON serialization. It does not validate data at runtime. Pydantic validates HTTP input, JSON Schema validates tool arguments, and the API independently enforces authorization and business rules.

## Run the tests you need

- `tests/test_conversations.py`: clarification, follow-up context, invented answers and values, response relevance.
- `tests/test_api_boundaries.py`: ownership, permissions, schema validation, API behavior.
- `tests/test_model_contracts.py`: structured provider requests and available decisions.
- `tests/test_workflow.py`: discovery, approval, restart checkpoints, idempotency and memory.
- `tests/conftest.py`: isolated databases and in-process HTTP services.

Use `python -m pytest -q`. Tests call the same API adapter used by the live loop, with an in-process HTTP transport. Scripted model tests are reproducible but do not prove that Hermes will choose the right steps. The saved browser evaluation addresses that separately.

## Deliberate limits

The request interpreter and response presenters are demo-specific rules. Their named functions make them easier to replace, but moving them into a module does not turn them into a general language-understanding system. The optional classifier is advisory; it does not authorize actions or provide a fully implemented model-routing strategy.

The model does not decide authorization. A plausible ID is not proof of ownership. The orders API checks both `tenant` and `owner` on every read and write. Neither a system prompt nor a chat claim such as “I am an admin” changes that.
