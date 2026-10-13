"""Web adapter only. The complete agent flow lives in loop.py; wiring in runtime.py."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import os

from . import models
from .common import USERS, bearer, decode_token, issue_token, principal
from .registry import CAPABILITIES, load_registry
from .runtime import build_runtime
from .state import create_run

SERVICES = {
    "orders": os.getenv("ORDERS_URL", "http://127.0.0.1:8766"),
    "documents": os.getenv("DOCUMENTS_URL", "http://127.0.0.1:8767"),
}
tasks, locks = set(), {}


def launch(coro):
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@asynccontextmanager
async def lifespan(app):
    app.state.runtime = build_runtime(await load_registry(SERVICES))
    app.state.runtime.store.initialize()
    yield
    for task in list(tasks):
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Agent Harness Lab", lifespan=lifespan)
app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)


def fetch(run_id, user):
    try:
        return app.state.runtime.store.get(run_id, user["sub"])
    except KeyError:
        raise HTTPException(404, "Run not found")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/source/{component}", include_in_schema=False)
def source(component: Literal["loop", "runtime"]):
    return FileResponse(
        Path(__file__).parent / (component + ".py"), media_type="text/plain"
    )


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


@app.get("/config")
def config():
    runtime = app.state.runtime
    return {
        "model": models.MODEL,
        "utility_model": models.UTILITY_MODEL,
        "model_url": models.BASE_URL,
        "capabilities": CAPABILITIES,
        "max_model_calls": runtime.guards.max_calls,
        "context_byte_guard": runtime.guards.max_request_bytes,
        "summary_trigger_bytes": runtime.memory.summary_trigger,
        "decision_format": models.DECISION_FORMAT,
        "protocol": models.PROTOCOL,
    }


class Session(BaseModel):
    user: Literal["alice", "viewer", "bob"] = "alice"


@app.post("/demo/session")
def session(body: Session):
    # Deliberately an identity switcher, NOT deployment authentication.
    return {"token": issue_token(body.user), "user": body.user, **USERS[body.user]}


class StartRun(BaseModel):
    prompt: str = Field(min_length=1, max_length=3000)
    mode: Literal["mock", "lmstudio"] = "lmstudio"
    discovery: Literal["lazy", "eager"] = "lazy"
    utility: bool = False
    previous_run: Optional[str] = None


@app.post("/runs", status_code=202)
async def start(body: StartRun, credentials=Depends(bearer)):
    user = decode_token(credentials.credentials)
    prior = fetch(body.previous_run, user) if body.previous_run else None
    try:
        state = create_run(
            body.prompt,
            user,
            mode=body.mode,
            discovery=body.discovery,
            utility=body.utility,
            previous=prior,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    runtime = app.state.runtime
    runtime.store.save(state)
    launch(runtime.run(state, user, credentials.credentials))
    return {"id": state["id"]}


@app.get("/runs")
def list_runs(user=Depends(principal)):
    return app.state.runtime.store.recent(user["sub"])


@app.get("/runs/{run_id}")
def get_run(run_id: str, user=Depends(principal)):
    return fetch(run_id, user)


@app.get("/runs/{run_id}/audit")
def audit(run_id: str, user=Depends(principal)):
    return {"run": fetch(run_id, user), "audit": app.state.runtime.audit.read(run_id)}


@app.get("/direct/orders/{order_id}")
async def direct_order(order_id: str, credentials=Depends(bearer)):
    decode_token(credentials.credentials)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            SERVICES["orders"] + "/orders/" + quote(order_id, safe=""),
            headers={"Authorization": "Bearer " + credentials.credentials},
        )
    return JSONResponse(response.json(), status_code=response.status_code)


class Approval(BaseModel):
    approve: bool


@app.post("/runs/{run_id}/approval")
async def approve(run_id: str, body: Approval, credentials=Depends(bearer)):
    user = decode_token(credentials.credentials)
    async with locks.setdefault(run_id, asyncio.Lock()):
        runtime = app.state.runtime
        state = fetch(run_id, user)
        try:
            runtime.approvals.decide(state, body.approve)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if not body.approve:
            runtime.memory.finish(state)
        runtime.store.save(state)
        if body.approve:
            launch(runtime.run(state, user, credentials.credentials, approved=True))
    return {"id": run_id, "status": state["status"]}
