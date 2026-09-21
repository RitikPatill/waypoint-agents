"""Integration tests for crash + resume (M5)."""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.agent import Agent
from waypoint.events import Event, EventType, append, list_events, migrate
from waypoint.events import list_running_runs
from waypoint.tools import Tool
from waypoint.workflow import Workflow, WorkflowRunner, resume_runs


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_engine.py)
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


# ---------------------------------------------------------------------------
# test_list_running_runs_excludes_finished
# ---------------------------------------------------------------------------

async def test_list_running_runs_excludes_finished(tmp_path):
    db = str(tmp_path / "events.db")
    migrate(db)

    # run-A: finished
    append(db, Event(run_id="run-A", type=EventType.RUN_STARTED, payload={"workflow_name": "w", "prompt": "x"}))
    append(db, Event(run_id="run-A", type=EventType.RUN_FINISHED, payload={"output": "done"}))

    # run-B: failed
    append(db, Event(run_id="run-B", type=EventType.RUN_STARTED, payload={"workflow_name": "w", "prompt": "x"}))
    append(db, Event(run_id="run-B", type=EventType.RUN_FAILED, payload={"error": "boom"}))

    # run-C: interrupted (only started)
    append(db, Event(run_id="run-C", type=EventType.RUN_STARTED, payload={"workflow_name": "w", "prompt": "x"}))

    running = list_running_runs(db)
    run_ids = [r[0] for r in running]

    assert run_ids == ["run-C"]
    assert running[0][1] == "w"


# ---------------------------------------------------------------------------
# test_resume_completes_with_exactly_once_tool_execution
# ---------------------------------------------------------------------------

async def test_resume_completes_with_exactly_once_tool_execution(tmp_path):
    db = str(tmp_path / "events.db")
    count_file = tmp_path / "count.txt"
    count_file.write_text("0")

    # Define a counting tool
    def _count_fn() -> str:
        current = int(count_file.read_text())
        count_file.write_text(str(current + 1))
        return "counted"

    counting_tool = Tool(
        name="counting_tool",
        description="Increments a counter file.",
        input_schema={"type": "object", "properties": {}, "required": []},
        fn=_count_fn,
        idempotency_key="counting_tool",
    )

    agent = Agent(
        name="counter_agent",
        system_prompt="You are a counter.",
        tools=[counting_tool],
    )
    workflow = Workflow(
        name="counter_test",
        agents={"counter_agent": agent},
        entry_point="counter_agent",
    )

    # Pre-seed DB to simulate: RUN_STARTED → AGENT_STEP_STARTED → LLM_CALL_COMMITTED
    # (tool_use) → TOOL_CALL_COMMITTED — then crash (no second LLM call)
    migrate(db)
    run_id = "run-crash-001"
    tool_use_id = "tu_count_1"

    content_payload = [
        {"type": "tool_use", "id": tool_use_id, "name": "counting_tool", "input": {}}
    ]

    append(db, Event(
        run_id=run_id,
        type=EventType.RUN_STARTED,
        agent=None,
        payload={"workflow_name": "counter_test", "prompt": "go"},
    ))
    append(db, Event(
        run_id=run_id,
        type=EventType.AGENT_STEP_STARTED,
        agent="counter_agent",
        payload={"agent": "counter_agent"},
    ))
    append(db, Event(
        run_id=run_id,
        type=EventType.LLM_CALL_STARTED,
        agent="counter_agent",
        payload={"agent": "counter_agent", "message_count": 1},
    ))
    append(db, Event(
        run_id=run_id,
        type=EventType.LLM_CALL_COMMITTED,
        agent="counter_agent",
        payload={
            "agent": "counter_agent",
            "content": content_payload,
            "stop_reason": "tool_use",
        },
    ))
    append(db, Event(
        run_id=run_id,
        type=EventType.TOOL_CALL_STARTED,
        agent="counter_agent",
        payload={
            "tool_use_id": tool_use_id,
            "tool_name": "counting_tool",
            "tool_input": {},
            "idempotency_key": "counting_tool",
        },
    ))
    # Tool already ran once (committed) — count.txt was hypothetically incremented
    # but we start at "0" to verify it stays at "0" after resume
    append(db, Event(
        run_id=run_id,
        type=EventType.TOOL_CALL_COMMITTED,
        agent="counter_agent",
        payload={
            "tool_use_id": tool_use_id,
            "tool_name": "counting_tool",
            "result": "counted",
        },
    ))
    # Crash here — second LLM call never happened

    # Mock client: single end_turn response for the resumed second LLM call
    end_turn_response = _response(
        stop_reason="end_turn",
        content=[_text_block("done")],
    )
    mock_client = _make_client(end_turn_response)

    # Resume
    resumed = await resume_runs(db, {"counter_test": workflow}, mock_client)

    assert run_ids_in(resumed, run_id)

    # Tool was NOT called again — count.txt still reads "0"
    assert count_file.read_text() == "0"

    # RUN_FINISHED was written
    events = list_events(db, run_id)
    finished = [e for e in events if e.type == EventType.RUN_FINISHED]
    assert len(finished) == 1


def run_ids_in(resumed: list[str], run_id: str) -> bool:
    return run_id in resumed
