"""Research team example: Planner → Researcher."""
import asyncio
import os
import uuid

import anthropic
from dotenv import load_dotenv

from waypoint.agent import Agent
from waypoint.builtin_tools import fetch_url
from waypoint.workflow import Workflow, WorkflowRunner

load_dotenv()

planner = Agent(
    name="planner",
    system_prompt=(
        "You are a research planner. Given a query, identify 2–3 specific URLs "
        "worth fetching to answer it. Then call the handoff tool with target='researcher' "
        "and a payload listing those URLs, one per line."
    ),
    tools=[],   # handoff tool is injected by WorkflowRunner
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


async def main(query: str = "State of MCP servers in 2026") -> None:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    runner = WorkflowRunner(db_path="waypoint.db")
    run_id = str(uuid.uuid4())
    print(f"run_id: {run_id}")
    output = await runner.run(run_id=run_id, workflow=research_team, prompt=query, client=client)
    print("\n=== Final summary ===")
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
