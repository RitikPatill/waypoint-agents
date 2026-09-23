"""Tests for the FastAPI backend (M6)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from waypoint.api import create_app
from waypoint.events import Event, EventType, append, migrate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "test.db"), dev_mode=True)


@pytest.fixture
def app_no_dev(tmp_path):
    return create_app(db_path=str(tmp_path / "test_nodev.db"), dev_mode=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_workflow_returns_404(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/runs", json={"workflow": "nonexistent", "prompt": "hi"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_not_found_returns_404(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/runs/fake-run-id-that-does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_kill_worker_forbidden_without_dev(app_no_dev):
    async with AsyncClient(transport=ASGITransport(app=app_no_dev), base_url="http://test") as client:
        resp = await client.post("/debug/kill-worker")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_run_returns_run_id(tmp_path, monkeypatch):
    """POST /runs returns 202 with run_id; WorkflowRunner.run is a no-op."""
    ran: list[str] = []

    async def _fake_run(self, run_id, workflow, prompt, client):
        ran.append(run_id)

    import waypoint.workflow as wf_mod
    monkeypatch.setattr(wf_mod.WorkflowRunner, "run", _fake_run)

    app = create_app(db_path=str(tmp_path / "test.db"), dev_mode=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/runs", json={"workflow": "research_team", "prompt": "test query"})

    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert isinstance(data["run_id"], str)
    # Give the background task a tick to run
    await asyncio.sleep(0.05)
    assert len(ran) == 1
    assert ran[0] == data["run_id"]


@pytest.mark.asyncio
async def test_get_run_status_from_events(tmp_path):
    db = str(tmp_path / "test.db")
    migrate(db)
    run_id = "test-run-abc"

    append(db, Event(run_id=run_id, type=EventType.RUN_STARTED, payload={"workflow_name": "research_team", "prompt": "hi"}))
    append(db, Event(run_id=run_id, type=EventType.RUN_FINISHED, payload={"output": "done"}))

    app = create_app(db_path=db, dev_mode=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["status"] == "finished"
    assert data["workflow"] == "research_team"
    assert data["event_count"] == 2


@pytest.mark.asyncio
async def test_sse_replays_history(tmp_path):
    """SSE endpoint replays historical events and closes when run is finished."""
    db = str(tmp_path / "test.db")
    migrate(db)
    run_id = "sse-run-xyz"

    append(db, Event(run_id=run_id, type=EventType.RUN_STARTED, payload={"workflow_name": "research_team", "prompt": "q"}))
    append(db, Event(run_id=run_id, type=EventType.RUN_FINISHED, payload={"output": "result"}))

    app = create_app(db_path=db, dev_mode=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", f"/runs/{run_id}/events") as resp:
            assert resp.status_code == 200
            lines = []
            async for line in resp.aiter_lines():
                lines.append(line)

    # Filter to data lines only
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) == 2

    types = [json.loads(l[len("data: "):])["type"] for l in data_lines]
    assert types[0] == "RUN_STARTED"
    assert types[1] == "RUN_FINISHED"
