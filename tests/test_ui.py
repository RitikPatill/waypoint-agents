"""Tests for M7: live timeline UI endpoints and RUN_RESUMED event."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from waypoint.api import create_app
from waypoint.events import Event, EventType, append, list_events, migrate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "test.db"), dev_mode=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_returns_html(app):
    """GET / returns 200 HTML containing 'Waypoint' and 'alpinejs'."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Waypoint" in resp.text
    assert "alpinejs" in resp.text


@pytest.mark.asyncio
async def test_list_workflows(app):
    """GET /workflows returns 200 with a dict containing 'workflows' list."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert "workflows" in data
    assert isinstance(data["workflows"], list)
    assert "research_team" in data["workflows"]


def test_run_resumed_in_event_type_enum():
    """EventType.RUN_RESUMED exists with value 'RUN_RESUMED'."""
    assert EventType.RUN_RESUMED == "RUN_RESUMED"
    assert EventType.RUN_RESUMED.value == "RUN_RESUMED"


@pytest.mark.asyncio
async def test_resume_emits_run_resumed_event(tmp_path, monkeypatch):
    """Pre-seeding a RUN_STARTED event causes WorkflowRunner.run to emit RUN_RESUMED."""
    from waypoint.agent import AgentResult
    from waypoint.engine import DurableRunner

    db = str(tmp_path / "test.db")
    migrate(db)

    run_id = "resume-test-run"

    # Pre-seed a RUN_STARTED event so the runner detects it as a resuming run
    append(db, Event(
        run_id=run_id,
        type=EventType.RUN_STARTED,
        payload={"workflow_name": "research_team", "prompt": "test prompt"},
    ))

    # Monkeypatch DurableRunner.run_agent to be a no-op that returns a finished result
    async def _noop_run_agent(self, run_id, agent, prompt, client):
        return AgentResult(output="done", messages=[], handoff=None)

    monkeypatch.setattr(DurableRunner, "run_agent", _noop_run_agent)

    from unittest.mock import MagicMock
    from waypoint.workflow import Workflow, WorkflowRunner
    from waypoint.agent import Agent

    agent = Agent(name="planner", system_prompt="You are a planner.", tools=[])
    workflow = Workflow(
        name="research_team",
        agents={"planner": agent},
        entry_point="planner",
    )

    runner = WorkflowRunner(db_path=db)
    await runner.run(run_id=run_id, workflow=workflow, prompt="test prompt", client=MagicMock())

    events = list_events(db, run_id)
    event_types = [e.type for e in events]
    assert EventType.RUN_RESUMED in event_types

    # RUN_RESUMED appears after RUN_STARTED
    resumed_idx = event_types.index(EventType.RUN_RESUMED)
    started_idx = event_types.index(EventType.RUN_STARTED)
    assert resumed_idx > started_idx
