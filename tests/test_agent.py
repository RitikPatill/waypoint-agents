"""Integration tests for Agent tool-use loop (mocked Anthropic client)."""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.agent import Agent, AgentResult
from waypoint.builtin_tools import sum_numbers
from waypoint.tools import Tool


# ---------------------------------------------------------------------------
# Helpers to build fake SDK response objects
# ---------------------------------------------------------------------------

def _text_block(text: str):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use_block(id: str, name: str, input: dict):
    return types.SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(stop_reason: str, content: list):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_tool_to_api_dict():
    assert sum_numbers.to_api_dict() == {
        "name": "sum_numbers",
        "description": "Return the sum of a list of numbers.",
        "input_schema": {
            "type": "object",
            "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
            "required": ["numbers"],
        },
    }


async def test_agent_end_turn_immediately():
    """If the first response has stop_reason=end_turn, run() returns without tool dispatch."""
    mock_create = AsyncMock(return_value=_response(
        stop_reason="end_turn",
        content=[_text_block("Done immediately.")],
    ))
    client = MagicMock()
    client.messages.create = mock_create

    agent = Agent(
        name="test",
        system_prompt="You are helpful.",
        tools=[sum_numbers],
    )
    result = await agent.run("Hello", client=client)

    assert result.output == "Done immediately."
    assert mock_create.await_count == 1


async def test_agent_uses_sum_tool_and_returns():
    """Full tool-use loop: two LLM calls, tool is invoked, result fed back."""
    first_response = _response(
        stop_reason="tool_use",
        content=[_tool_use_block(id="tu_1", name="sum_numbers", input={"numbers": [1, 2, 3]})],
    )
    second_response = _response(
        stop_reason="end_turn",
        content=[_text_block("The answer is 6.")],
    )

    mock_create = AsyncMock(side_effect=[first_response, second_response])
    client = MagicMock()
    client.messages.create = mock_create

    agent = Agent(
        name="calculator",
        system_prompt="You are a calculator.",
        tools=[sum_numbers],
    )
    result = await agent.run("What is 1 + 2 + 3?", client=client)

    assert result.output == "The answer is 6."
    assert mock_create.await_count == 2

    # Verify the second call includes a tool_result block with the sum.
    # Because `messages` is a mutable list passed by reference, it gains the
    # final assistant turn after the second call returns. So index [-2] is the
    # tool_result turn that was present when the second call was made.
    second_call_messages = mock_create.call_args_list[1].kwargs["messages"]
    tool_result_turn = second_call_messages[-2]  # [-1] is the post-call assistant append
    assert tool_result_turn["role"] == "user"
    tool_result_block = tool_result_turn["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert tool_result_block["tool_use_id"] == "tu_1"
    # sum([1,2,3]) == 6; content must be a string
    assert tool_result_block["content"] in ("6", "6.0")
