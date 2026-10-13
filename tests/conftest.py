"""Isolated SQLite stores and in-process downstream HTTP services."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lab import harness, models, orders, documents
from lab.common import issue_token, decode_token
from lab.registry import Registry, SEARCH


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    orders.initialize()
    registry = Registry()
    registry.add("orders", "http://orders", orders.app.openapi())
    registry.add("documents", "http://documents", documents.app.openapi())
    from lab.runtime import build_runtime

    harness.app.state.runtime = build_runtime(registry)

    class ServiceTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            app = documents.app if request.url.host == "documents" else orders.app
            return await httpx.ASGITransport(app=app).handle_async_request(request)

    transport = ServiceTransport()
    real_client = httpx.AsyncClient

    class LocalClient(real_client):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(harness.httpx, "AsyncClient", LocalClient)
    from lab.common import database

    with database("harness") as db:
        db.execute("CREATE TABLE runs(id TEXT PRIMARY KEY, owner TEXT, body TEXT)")
        db.execute(
            "CREATE TABLE events(id INTEGER PRIMARY KEY, run_id TEXT, body TEXT)"
        )
    return registry
