"""Tests for the durable engine: write-ahead pattern and replay-based resumption."""
from __future__ import annotations

import json
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.agent import Agent
from waypoint.engine import DurableRunner, replay
from waypoint.events import Event, EventType, append, migrate
from waypoint.tools import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_block(text: str):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use_block(id: str, name: str, input: dict):
    return types.SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(stop_reason: str, content: list):
    ns = types.SimpleNamespace(stop_reason=stop_reason, content=content)
    # Give content blocks a model_dump equivalent via __dict__ fallback
    return ns


def _make_agent(tools=None) -> Agent:
    return Agent(
        name="test_agent",
        system_prompt="You are helpful.",
        tools=tools or [],
    )


def _make_client(*responses) -> MagicMock:
    mock_create = AsyncMock(side_effect=list(responses))
    client = MagicMock()
    client.messages.create = mock_create
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_replay_empty(tmp_path):
    """Fresh run_id returns ReplayState with empty collections and status=running."""
    db = str(tmp_path / "events.db")
    migrate(db)
    state = replay(db, "run-001")
    assert state.status == "running"
    assert state.committed_llm_responses == []
    assert state.committed_tool_results == {}


async def test_write_ahead_happy_path(tmp_path):
    """Single end_turn response: LLM_CALL_STARTED before COMMITTED, finishes correctly."""
    db = str(tmp_path / "events.db")

    end_turn_response = _response(
        stop_reason="end_turn",
        content=[_text_block("All done.")],
    )
    client = _make_client(end_turn_response)
    agent = _make_agent()

    runner = DurableRunner(db_path=db)
    result = await runner.run_agent(
        run_id="run-001",
        agent=agent,
        prompt="Say hi.",
        client=client,
    )

    assert result.output == "All done."

    # Verify event ordering
    from waypoint.events import list_events
    events = list_events(db, "run-001")
    types_in_order = [e.type for e in events]

    started_idx = types_in_order.index(EventType.LLM_CALL_STARTED)
    committed_idx = types_in_order.index(EventType.LLM_CALL_COMMITTED)
    finished_idx = types_in_order.index(EventType.AGENT_STEP_FINISHED)

    assert started_idx < committed_idx
    assert committed_idx < finished_idx
    assert types_in_order[-1] == EventType.AGENT_STEP_FINISHED

    # LLM was called exactly once
    assert client.messages.create.await_count == 1


async def test_resume_after_crash_between_llm_writes(tmp_path):
    """Crash between LLM_CALL_STARTED and LLM_CALL_COMMITTED: re-execute on resume."""
    db = str(tmp_path / "events.db")
    migrate(db)

    # Simulate crash: only the STARTED event was written
    append(db, Event(
        run_id="run-002",
        type=EventType.LLM_CALL_STARTED,
        agent="test_agent",
        payload={"agent": "test_agent", "message_count": 1},
    ))

    # Resume: the started event has no committed pair, so replay ignores it
    end_turn_response = _response(
        stop_reason="end_turn",
        content=[_text_block("Resumed fine.")],
    )
    client = _make_client(end_turn_response)
    agent = _make_agent()

    runner = DurableRunner(db_path=db)
    result = await runner.run_agent(
        run_id="run-002",
        agent=agent,
        prompt="Retry.",
        client=client,
    )

    assert result.output == "Resumed fine."
    # LLM was called again (not skipped)
    assert client.messages.create.await_count == 1


async def test_tool_result_replayed_not_re_executed(tmp_path):
    """Tool already committed in log: fn is never called on resume."""
    db = str(tmp_path / "events.db")
    migrate(db)

    run_id = "run-003"

    # Insert committed LLM response with tool_use stop reason
    tool_use_id = "tu_abc"
    content_payload = [
        {"type": "tool_use", "id": tool_use_id, "name": "my_tool", "input": {"x": 1}}
    ]
    append(db, Event(
        run_id=run_id,
        type=EventType.LLM_CALL_COMMITTED,
        agent="test_agent",
        payload={
            "agent": "test_agent",
            "content": content_payload,
            "stop_reason": "tool_use",
        },
    ))
    # Insert committed tool result
    append(db, Event(
        run_id=run_id,
        type=EventType.TOOL_CALL_COMMITTED,
        agent="test_agent",
        payload={
            "tool_use_id": tool_use_id,
            "tool_name": "my_tool",
            "result": "stored_result",
        },
    ))

    # Second LLM call: end_turn
    end_turn_response = _response(
        stop_reason="end_turn",
        content=[_text_block("Tool result was replayed.")],
    )
    client = _make_client(end_turn_response)

    tool_fn = MagicMock(return_value="live_result")
    my_tool = Tool(
        name="my_tool",
        description="test tool",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        fn=tool_fn,
        idempotency_key="my_tool",
    )
    agent = _make_agent(tools=[my_tool])

    runner = DurableRunner(db_path=db)
    result = await runner.run_agent(
        run_id=run_id,
        agent=agent,
        prompt="Go.",
        client=client,
    )

    assert result.output == "Tool result was replayed."
    # Tool fn was never called — result came from log
    tool_fn.assert_not_called()
    # Second LLM call was made
    assert client.messages.create.await_count == 1


async def test_crash_between_tool_writes(tmp_path):
    """TOOL_CALL_STARTED without COMMITTED: tool re-executes on resume."""
    db = str(tmp_path / "events.db")
    migrate(db)

    run_id = "run-004"
    tool_use_id = "tu_xyz"

    # Insert committed LLM response (tool_use stop)
    content_payload = [
        {"type": "tool_use", "id": tool_use_id, "name": "my_tool", "input": {"x": 5}}
    ]
    append(db, Event(
        run_id=run_id,
        type=EventType.LLM_CALL_COMMITTED,
        agent="test_agent",
        payload={
            "agent": "test_agent",
            "content": content_payload,
            "stop_reason": "tool_use",
        },
    ))
    # Only STARTED, no COMMITTED — simulates crash after write-ahead but before commit
    append(db, Event(
        run_id=run_id,
        type=EventType.TOOL_CALL_STARTED,
        agent="test_agent",
        payload={
            "tool_use_id": tool_use_id,
            "tool_name": "my_tool",
            "tool_input": {"x": 5},
            "idempotency_key": None,
        },
    ))

    # On resume, tool re-executes; second LLM call returns end_turn
    end_turn_response = _response(
        stop_reason="end_turn",
        content=[_text_block("Tool re-ran successfully.")],
    )
    client = _make_client(end_turn_response)

    tool_fn = MagicMock(return_value="fresh_result")
    my_tool = Tool(
        name="my_tool",
        description="test tool",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        fn=tool_fn,
    )
    agent = _make_agent(tools=[my_tool])

    runner = DurableRunner(db_path=db)
    result = await runner.run_agent(
        run_id=run_id,
        agent=agent,
        prompt="Resume.",
        client=client,
    )

    assert result.output == "Tool re-ran successfully."
    # Tool fn was called (re-executed because COMMITTED was missing)
    tool_fn.assert_called_once()

    # TOOL_CALL_COMMITTED was appended this time
    from waypoint.events import list_events
    events = list_events(db, run_id)
    committed_events = [e for e in events if e.type == EventType.TOOL_CALL_COMMITTED]
    assert len(committed_events) == 1
    assert committed_events[0].payload["tool_use_id"] == tool_use_id
