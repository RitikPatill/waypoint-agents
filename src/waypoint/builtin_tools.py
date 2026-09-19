from __future__ import annotations

import httpx

from waypoint.tools import Tool


def _fetch_url_sync(url: str) -> str:
    response = httpx.get(url, follow_redirects=True, timeout=15)
    return response.text[:4000]


fetch_url = Tool(
    name="fetch_url",
    description="Fetch the text content of a URL.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    fn=_fetch_url_sync,
)

sum_numbers = Tool(
    name="sum_numbers",
    description="Return the sum of a list of numbers.",
    input_schema={
        "type": "object",
        "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
        "required": ["numbers"],
    },
    fn=lambda numbers: sum(numbers),
)
