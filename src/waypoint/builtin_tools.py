from __future__ import annotations

from pathlib import Path

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


def make_read_file_tool(allowed_dirs: list[Path]) -> Tool:
    """Factory that returns a read_file Tool restricted to allowed_dirs."""
    resolved_dirs = [d.resolve() for d in allowed_dirs]

    def _read_file(file_path: str) -> str:
        resolved = Path(file_path).resolve()
        if not any(resolved.is_relative_to(d) for d in resolved_dirs):
            return f"Error: path '{file_path}' is not allowed. Access is restricted to permitted directories."
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading file: {exc}"
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated]"
        return content

    return Tool(
        name="read_file",
        description="Read the text content of a local file.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read",
                }
            },
            "required": ["file_path"],
        },
        fn=_read_file,
    )
