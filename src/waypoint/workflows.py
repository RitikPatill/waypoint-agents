"""Built-in workflow registry — single source of truth for API and CLI."""
from __future__ import annotations

from pathlib import Path

from waypoint.agent import Agent
from waypoint.builtin_tools import fetch_url, make_read_file_tool
from waypoint.workflow import Workflow

planner = Agent(
    name="planner",
    system_prompt=(
        "You are a research planner. Given a query, identify 2–3 specific URLs "
        "worth fetching to answer it. Then call the handoff tool with target='researcher' "
        "and a payload listing those URLs, one per line."
    ),
    tools=[],  # handoff tool is injected by WorkflowRunner
)

researcher = Agent(
    name="researcher",
    system_prompt=(
        "You are a researcher. You will receive a list of URLs. "
        "Fetch each one with the fetch_url tool, then write a concise summary."
    ),
    tools=[fetch_url],
)

research_team = Workflow(
    name="research_team",
    agents={"planner": planner, "researcher": researcher},
    entry_point="planner",
)

# ---------------------------------------------------------------------------
# Code reviewer workflow
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "fixtures"
read_file = make_read_file_tool(allowed_dirs=[_FIXTURES_DIR])

reader = Agent(
    name="reader",
    system_prompt=(
        "You are a code reader. Use the read_file tool to read the file path you are given. "
        "Then call the handoff tool with target='critic' and a payload containing the full "
        "source code you just read."
    ),
    tools=[read_file],
)

critic = Agent(
    name="critic",
    system_prompt=(
        "You are a code critic. You will receive Python source code. "
        "Identify all bugs, code smells, and style issues, referencing approximate line numbers. "
        "Be specific and exhaustive. Then call the handoff tool with target='summarizer' "
        "and a payload containing your full critique."
    ),
    tools=[],
)

summarizer = Agent(
    name="summarizer",
    system_prompt=(
        "You are a review summarizer. You will receive a detailed code critique. "
        "Produce a concise, prioritised action list (P1/P2/P3) a developer can act on immediately. "
        "Do not add new findings — only distil what you received."
    ),
    tools=[],
)

code_reviewer = Workflow(
    name="code_reviewer",
    agents={"reader": reader, "critic": critic, "summarizer": summarizer},
    entry_point="reader",
)

BUILTIN_WORKFLOWS: dict[str, Workflow] = {
    "research_team": research_team,
    "code_reviewer": code_reviewer,
}
