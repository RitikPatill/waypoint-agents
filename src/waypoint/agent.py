from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anthropic

from waypoint.tools import Tool, ToolResult

if TYPE_CHECKING:
    from waypoint.workflow import Handoff


@dataclass
class AgentResult:
    output: str
    messages: list
    handoff: "Handoff | None" = None


@dataclass
class Agent:
    name: str
    system_prompt: str
    tools: list[Tool]
    model: str = "claude-opus-4-6"

    async def run(
        self,
        prompt: str,
        client: anthropic.AsyncAnthropic,
    ) -> AgentResult:
        messages: list = [{"role": "user", "content": prompt}]

        while True:
            response = await client.messages.create(
                model=self.model,
                system=self.system_prompt,
                tools=[t.to_api_dict() for t in self.tools],
                messages=messages,
                max_tokens=4096,
            )

            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract first text block
                for block in response.content:
                    if block.type == "text":
                        return AgentResult(output=block.text, messages=messages)
                # Fallback: no text block found
                return AgentResult(output="", messages=messages)

            if response.stop_reason == "tool_use":
                tool_results = await self._dispatch_tools(response.content)
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

    async def _dispatch_tools(self, content: list) -> list[ToolResult]:
        tool_map = {t.name: t for t in self.tools}
        tasks = []
        use_ids = []

        for block in content:
            if block.type == "tool_use":
                tool = tool_map.get(block.name)
                if tool is None:
                    raise ValueError(f"Unknown tool: {block.name}")
                tasks.append(tool.dispatch(**block.input))
                use_ids.append(block.id)

        results = await asyncio.gather(*tasks)
        return [
            ToolResult(tool_use_id=uid, content=str(result))
            for uid, result in zip(use_ids, results)
        ]
