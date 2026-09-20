"""Durable agent runner with write-ahead event log and replay-based resumption."""
from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from waypoint.events import Event, EventType, append, list_events, migrate
from waypoint.tools import ToolResult

if TYPE_CHECKING:
    import anthropic

    from waypoint.agent import Agent, AgentResult


@dataclass
class ReplayState:
    run_id: str
    # ordered list of committed LLM response payloads
    committed_llm_responses: list[dict] = field(default_factory=list)
    # tool_use_id -> result string
    committed_tool_results: dict[str, str] = field(default_factory=dict)
    status: str = "running"  # "running" | "finished" | "failed"


def replay(db_path: str, run_id: str) -> ReplayState:
    """Pure fold over the event log — no side effects."""
    state = ReplayState(run_id=run_id)
    for event in list_events(db_path, run_id):
        if event.type == EventType.LLM_CALL_COMMITTED:
            state.committed_llm_responses.append(event.payload)
        elif event.type == EventType.TOOL_CALL_COMMITTED:
            state.committed_tool_results[event.payload["tool_use_id"]] = event.payload["result"]
        elif event.type == EventType.RUN_FINISHED:
            state.status = "finished"
        elif event.type == EventType.RUN_FAILED:
            state.status = "failed"
    return state


def _blocks_to_payload(content: list) -> list[dict]:
    """Convert SDK response content blocks to plain dicts for storage."""
    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            result.append(block.model_dump())
        else:
            result.append(dict(vars(block)))
    return result


def _payload_to_blocks(payload_blocks: list[dict]) -> list:
    """Reconstruct lightweight namespace objects from stored dicts."""
    blocks = []
    for d in payload_blocks:
        ns = types.SimpleNamespace(**d)
        blocks.append(ns)
    return blocks


class DurableRunner:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        migrate(db_path)

    async def run_agent(
        self,
        run_id: str,
        agent: "Agent",
        prompt: str,
        client: "anthropic.AsyncAnthropic",
    ) -> "AgentResult":
        from waypoint.agent import AgentResult

        state = replay(self.db_path, run_id)
        llm_cursor = 0

        messages: list = [{"role": "user", "content": prompt}]
        tool_map = {t.name: t for t in agent.tools}

        while True:
            # --- LLM step ---
            if llm_cursor < len(state.committed_llm_responses):
                # Replay: reconstruct response from stored payload
                stored = state.committed_llm_responses[llm_cursor]
                content_blocks = _payload_to_blocks(stored.get("content", []))
                stop_reason = stored.get("stop_reason", "end_turn")
            else:
                # Live: write-ahead then call API
                append(
                    self.db_path,
                    Event(
                        run_id=run_id,
                        type=EventType.LLM_CALL_STARTED,
                        agent=agent.name,
                        payload={"agent": agent.name, "message_count": len(messages)},
                    ),
                )
                response = await client.messages.create(
                    model=agent.model,
                    system=agent.system_prompt,
                    tools=[t.to_api_dict() for t in agent.tools],
                    messages=messages,
                    max_tokens=4096,
                )
                payload_blocks = _blocks_to_payload(response.content)
                append(
                    self.db_path,
                    Event(
                        run_id=run_id,
                        type=EventType.LLM_CALL_COMMITTED,
                        agent=agent.name,
                        payload={
                            "agent": agent.name,
                            "content": payload_blocks,
                            "stop_reason": response.stop_reason,
                        },
                    ),
                )
                content_blocks = _payload_to_blocks(payload_blocks)
                stop_reason = response.stop_reason

            llm_cursor += 1
            messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason == "end_turn":
                append(
                    self.db_path,
                    Event(
                        run_id=run_id,
                        type=EventType.AGENT_STEP_FINISHED,
                        agent=agent.name,
                        payload={"agent": agent.name},
                    ),
                )
                # Extract first text block
                for block in content_blocks:
                    if getattr(block, "type", None) == "text":
                        return AgentResult(output=block.text, messages=messages)
                return AgentResult(output="", messages=messages)

            if stop_reason == "tool_use":
                tool_results: list[ToolResult] = []
                for block in content_blocks:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    tool_use_id = block.id
                    tool_name = block.name
                    tool_input = block.input

                    if tool_use_id in state.committed_tool_results:
                        # Replay: use stored result
                        result_str = state.committed_tool_results[tool_use_id]
                    else:
                        # Live: write-ahead, execute, commit
                        tool = tool_map.get(tool_name)
                        if tool is None:
                            raise ValueError(f"Unknown tool: {tool_name}")
                        idempotency_key = getattr(tool, "idempotency_key", None)
                        append(
                            self.db_path,
                            Event(
                                run_id=run_id,
                                type=EventType.TOOL_CALL_STARTED,
                                agent=agent.name,
                                payload={
                                    "tool_use_id": tool_use_id,
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                    "idempotency_key": idempotency_key,
                                },
                            ),
                        )
                        result_str = str(await tool.dispatch(**tool_input))
                        append(
                            self.db_path,
                            Event(
                                run_id=run_id,
                                type=EventType.TOOL_CALL_COMMITTED,
                                agent=agent.name,
                                payload={
                                    "tool_use_id": tool_use_id,
                                    "tool_name": tool_name,
                                    "result": result_str,
                                },
                            ),
                        )

                    tool_results.append(ToolResult(tool_use_id=tool_use_id, content=result_str))

                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.tool_use_id,
                            "content": tr.content,
                        }
                        for tr in tool_results
                    ],
                })
            else:
                # Unknown stop reason — treat as done
                break

        return AgentResult(output="", messages=messages)
