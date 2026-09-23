"""Built-in workflow registry — single source of truth for API and CLI."""
from __future__ import annotations

from waypoint.agent import Agent
from waypoint.builtin_tools import fetch_url
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

BUILTIN_WORKFLOWS: dict[str, Workflow] = {
    "research_team": research_team,
}
