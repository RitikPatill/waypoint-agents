from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict  # JSON Schema {"type":"object","properties":...}
    fn: Callable[..., Any]  # sync or async

    def to_api_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    async def dispatch(self, **kwargs: Any) -> Any:
        if asyncio.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        return await asyncio.to_thread(self.fn, **kwargs)


@dataclass
class ToolResult:
    tool_use_id: str
    content: str
