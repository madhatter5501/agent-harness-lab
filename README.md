# Agent Harness Lab

A local Python teaching appliance: one explicit agent loop, a chat interface, an inspectable audit trail, and two downstream APIs. Humans and the agent call the same canonical order and policy endpoints.

**Start reading:** [the loop](lab/loop.py), [dependency wiring](lab/runtime.py), then the [junior-developer walkthrough](docs/WALKTHROUGH.md).

## Run it

```sh
cd examples/agent-harness-lab
docker compose up --build -d --wait
```

Open **http://localhost:8765**. Start LM Studio's local server and load `hermes-3-llama-3.1-8b`. Containers reach it at `host.docker.internal:1234/v1`; host-run Python uses `127.0.0.1:1234/v1`. Configuration examples are in `.env.example`.

The default is constrained JSON decisions, because this Hermes configuration did not reliably emit native tool calls. Mock mode is a scripted fixture for simple examples, not an intelligent assistant. No automatic cloud fallback occurs.

The page provides chat, a new-conversation button, identity/model controls, exact write approval, current token counts, an execution trace, and audit download. Enter sends a message; Shift+Enter adds a line. Replies continue the selected conversation by default.

Try these exchanges:

- “Where is my order?” → answer the clarification with “O-300.”
- “Show all my orders.” → “Which were cancelled, and why?”
- As Bob: “Cancel O-200 because I bought it twice.” → decline → “Did you actually cancel it?”
- “What warranty does my standing desk have?” → “And my desk lamp?”

## One loop, small components

```text
validate identity and input
interpret the current request / clarify missing information
prepare bounded working context
repeat:
    obtain one structured model decision
    question → save needs_input and return
    final → verify evidence, compose the answer, save and return
    discovery → load authorized schemas and continue
    action → validate intent, arguments and permissions
             pause for exact write approval when needed
             call the canonical API, record its result, continue
```

`lab/loop.py` contains the orchestration. It does not implement business rules, provider wire protocols, request interpretation, or response formatting. Components are injected in `lab/runtime.py`.

The [walkthrough](docs/WALKTHROUGH.md) explains each component, state, and test file. The UI can show the loop source directly.

## Ownership and identities

| Demo identity | Owned fixture orders | Permission |
|---|---|---|
| Alice | O-100, O-300 | Read and cancellation |
| Viewer | None initially | Read only |
| Bob | O-200 | Read and cancellation |

Alice and Viewer share a tenant, but they do **not** share order ownership. “My orders” lists orders whose owner matches the authenticated user. A single-order read or cancellation checks both owner and tenant. This is enforced by the canonical API, not inferred by the model.

Existing fixture order states are preserved across restarts. The ownership migration assigns owners only to the three known demo fixture IDs; unknown existing rows are not assigned by guesswork.

## Evidence, invented values, and clarification

- Tool schemas come from downstream OpenAPI. Only structured decisions execute; prose that resembles a tool call never executes.
- The demo request policy records the current target, write intent, and required API observations.
- Required tool parameters are narrowed to unresolved targets. Final answers are unavailable in JSON decision mode until required observations exist.
- Guards reject targets unsupported by the current request or authorized observations. Unprovided cancellation reasons pause for clarification.
- The API rechecks scope, tenant and owner. All writes require approval and use a stable idempotency key.
- Supported operational answers are composed from API fields. The audit records both proposed model prose and the delivered answer, with source observations.
- Missing facts are acknowledged; an unknown tracking number or cancellation timestamp is not filled in.
- `needs_input` preserves an unresolved goal and resumes it when the user replies. It is separate from approving a write.

These are scoped checks for the demo vocabulary. They are not a universal hallucination detector. General conversation is not independently verified, and the request interpreter can still misunderstand language outside its tested patterns.

## The canonical APIs

- `GET /orders?status=open|all`: the signed-in user's orders. Open means processing. At most 100 rows; `has_more` signals additional matches.
- `GET /orders/{order_id}`: status, item, and recorded cancellation reason. A null reason means no reason is available.
- `POST /orders/{order_id}/cancel`: an unshipped order, with a reason and `Idempotency-Key` header.
- `GET /documents/search?q=...`: matching policy articles. Unknown topics can return no matches.

Swagger: http://localhost:8766/docs and http://localhost:8767/docs. Obtain a demo token from `POST http://localhost:8765/demo/session` with `{"user":"alice"}`; send it as a Bearer token. The identity selector is intentionally local demo authentication, not production login.

The adapter supports GET/POST, scalar path/query parameters, local schema references, and JSON bodies. Unsupported OpenAPI operations fail startup. It is not a full OpenAPI client generator or MCP implementation.

## Memory, audit, and limits

Recent conversation plus a summary is working memory; SQLite retains original run/event records. Recent exact API observations are retained with provenance and treated as historical. Current operational questions require fresh matching evidence. Summary compression begins at 6 KB and is lossy.

Limits: eight model calls per turn, two final-answer correction attempts, 700 output tokens by default, a 24 KB serialized-request guard, and a 32 KB tool-response guard. These are bounded-execution measures, not exact context-token accounting. Provider token usage is recorded after responses; missing usage is labeled. There is no precise dollar budget or exact local tokenizer integration yet.

A pending approval survives restart. Running work is marked interrupted; this is not a general durable workflow engine with automatic replay. The SQLite audit is inspectable but not tamper-proof, and request data is retained. Credentials are omitted from ordinary trace events.

## Tests and browser evaluation

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

Python 3.9+; Docker uses Python 3.12. To run without Docker, use `python run_local.py` after installing requirements.

**50 automated cases** cover conversation recovery, API ownership, unsupported/invented arguments, provider contracts, approval, idempotency, memory, and state isolation. Separately, two 50-question conversation passes were submitted through the browser using the real local Hermes model. These are different kinds of evidence; test counts do not imply universal correctness.

See the [conversation evaluation](docs/evaluations/REPORT.md), [baseline](docs/evaluations/baseline-50.json), and [retest](docs/evaluations/retest-50.json). The baseline exposed failures that unit tests missed. The retest reached appropriate answers/clarifications/approval pauses in 49 cases; the remaining invented-reason case led to the clarification-recovery fix and its additional tests.

The optional cloud Responses adapter has protocol tests but has not been exercised against a paid provider.

## Development and boundaries

The frontend is split into HTML, CSS, and JavaScript. Python is formatted with Black 25.1.0; frontend files with Prettier 3.6.2. No frontend build system is required.

This remains a teaching prototype. Production work includes real identity/delegation, robust workflow recovery, concurrency limits, exact tokenizer/budget integration, broader semantic evaluation, retention/redaction, and operational monitoring. Advisory classification does not yet provide a complete utility/frontier model-routing strategy.

```sh
docker compose logs -f harness
docker compose down
```

`down` preserves orders and audit history. Do not remove volumes unless you intentionally want to erase the demo data.
