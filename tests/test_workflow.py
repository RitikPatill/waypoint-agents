"""Tests for multi-agent workflows and handoffs."""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.agent import Agent
from waypoint.engine import replay
from waypoint.events import Event, EventType, append, list_events, migrate
from waypoint.tools import Tool
from waypoint.workflow import Handoff, HandoffSignal, Workflow, WorkflowRunner


# ---------------------------------------------------------------------------
# Helpers (mirrors test_engine.py helpers)
# ---------------------------------------------------------------------------

def _text_block(text: str):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use_block(id: str, name: str, input: dict):
    return types.SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(stop_reason: str, content: list):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


def _make_client(*responses) -> MagicMock:
    mock_create = AsyncMock(side_effect=list(responses))
    client = MagicMock()
    client.messages.create = mock_create
    return client


def _make_two_agent_workflow() -> Workflow:
    planner = Agent(
        name="planner",
        system_prompt="You are a planner.",
        tools=[],
    )
    researcher = Agent(
        name="researcher",
        system_prompt="You are a researcher.",
        tools=[],
    )
    return Workflow(
        name="research_team",
        agents={"planner": planner, "researcher": researcher},
        entry_point="planner",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_workflow_handoff_events(tmp_path):
    """Planner calls handoff → Researcher end_turn: verify event order."""
    db = str(tmp_path / "events.db")

    # Planner returns tool_use for handoff
    planner_response = _response(
        stop_reason="tool_use",
        content=[_tool_use_block("tu_handoff_001", "handoff", {
            "target": "researcher",
            "payload": "fetch these URLs",
        })],
    )
    # Researcher returns end_turn with a text block
    researcher_response = _response(
        stop_reason="end_turn",
        content=[_text_block("Final summary here.")],
    )
    client = _make_client(planner_response, researcher_response)

    workflow = _make_two_agent_workflow()
    runner = WorkflowRunner(db_path=db)
    output = await runner.run(
        run_id="wf-001",
        workflow=workflow,
        prompt="Research something.",
        client=client,
    )

    assert output == "Final summary here."

    events = list_events(db, "wf-001")

    def idx(type_: EventType, agent: str | None = None) -> int:
        for i, e in enumerate(events):
            if e.type == type_ and (agent is None or e.agent == agent):
                return i
        raise AssertionError(f"Event {type_} (agent={agent}) not found in {[e.type for e in events]}")

    run_started = idx(EventType.RUN_STARTED)
    planner_step = idx(EventType.AGENT_STEP_STARTED, "planner")
    planner_llm_started = idx(EventType.LLM_CALL_STARTED, "planner")
    planner_llm_committed = idx(EventType.LLM_CALL_COMMITTED, "planner")
    handoff_committed = idx(EventType.HANDOFF_COMMITTED)
    researcher_step = idx(EventType.AGENT_STEP_STARTED, "researcher")
    researcher_llm_started = idx(EventType.LLM_CALL_STARTED, "researcher")
    researcher_llm_committed = idx(EventType.LLM_CALL_COMMITTED, "researcher")
    researcher_step_finished = idx(EventType.AGENT_STEP_FINISHED, "researcher")
    run_finished = idx(EventType.RUN_FINISHED)

    assert run_started < planner_step
    assert planner_step < planner_llm_started < planner_llm_committed < handoff_committed
    assert handoff_committed < researcher_step < researcher_llm_started < researcher_llm_committed
    assert researcher_llm_committed < researcher_step_finished < run_finished

    # Handoff payload is correct
    handoff_event = events[handoff_committed]
    assert handoff_event.payload["target"] == "researcher"
    assert handoff_event.agent == "planner"

    # TOOL_CALL_COMMITTED does NOT appear — handoff never produces a committed tool result
    assert not any(e.type == EventType.TOOL_CALL_COMMITTED for e in events)

    # Mock client called exactly twice: once for planner, once for researcher
    assert client.messages.create.await_count == 2


async def test_workflow_replays_planner_skips_researcher_redo(tmp_path):
    """Pre-seeded planner+handoff: only researcher makes a live LLM call on resume."""
    db = str(tmp_path / "events.db")
    migrate(db)

    run_id = "wf-002"

    # Pre-seed planner's committed LLM response (tool_use for handoff)
    append(db, Event(
        run_id=run_id,
        type=EventType.LLM_CALL_COMMITTED,
        agent="planner",
        payload={
            "agent": "planner",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_handoff_002",
                    "name": "handoff",
                    "input": {"target": "researcher", "payload": "fetch these URLs"},
                }
            ],
            "stop_reason": "tool_use",
        },
    ))
    # Pre-seed committed handoff
    append(db, Event(
        run_id=run_id,
        type=EventType.HANDOFF_COMMITTED,
        agent="planner",
        payload={"target": "researcher", "payload": "fetch these URLs"},
    ))

    # Only researcher will make a live LLM call
    researcher_response = _response(
        stop_reason="end_turn",
        content=[_text_block("Resumed summary.")],
    )
    client = _make_client(researcher_response)

    workflow = _make_two_agent_workflow()
    runner = WorkflowRunner(db_path=db)
    output = await runner.run(
        run_id=run_id,
        workflow=workflow,
        prompt="Research something.",
        client=client,
    )

    assert output == "Resumed summary."

    # Only one LLM call (researcher) — planner was fully replayed from log
    assert client.messages.create.await_count == 1

    # No duplicate HANDOFF_COMMITTED events
    events = list_events(db, run_id)
    handoff_events = [e for e in events if e.type == EventType.HANDOFF_COMMITTED]
    assert len(handoff_events) == 1
    assert handoff_events[0].payload["target"] == "researcher"
