"""Workflow: a directed graph of Agent nodes with typed handoff edges."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic

from waypoint.agent import Agent
from waypoint.engine import DurableRunner
from waypoint.events import Event, EventType, append
from waypoint.tools import Tool

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Handoff primitives
# ---------------------------------------------------------------------------

@dataclass
class Handoff:
    target: str
    payload: str


class HandoffSignal(Exception):
    """Raised by the handoff tool to signal control transfer."""
    def __init__(self, target: str, payload: str) -> None:
        self.target = target
        self.payload = payload


def make_handoff_tool(valid_targets: list[str]) -> Tool:
    """Return a Tool named 'handoff' that triggers a HandoffSignal when called."""
    targets_desc = ", ".join(f'"{t}"' for t in valid_targets)

    def _handoff(target: str, payload: str) -> str:
        raise HandoffSignal(target=target, payload=payload)

    return Tool(
        name="handoff",
        description=(
            f"Hand off control to another agent. valid targets: {targets_desc}. "
            "Call this as the LAST action in your turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": valid_targets,
                    "description": "Name of the agent to hand off to.",
                },
                "payload": {
                    "type": "string",
                    "description": "Message / context to pass to the next agent.",
                },
            },
            "required": ["target", "payload"],
        },
        fn=_handoff,
    )


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

@dataclass
class Workflow:
    name: str
    agents: dict[str, Agent]   # agent_name -> Agent
    entry_point: str            # name of the first agent to run


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------

class WorkflowRunner:
    def __init__(self, db_path: str) -> None:
        self.runner = DurableRunner(db_path)
        self.db_path = db_path

    async def run(
        self,
        run_id: str,
        workflow: Workflow,
        prompt: str,
        client: anthropic.AsyncAnthropic,
    ) -> str:
        append(self.db_path, Event(
            run_id=run_id,
            type=EventType.RUN_STARTED,
            payload={"workflow": workflow.name, "prompt": prompt},
        ))

        current_agent_name = workflow.entry_point
        current_prompt = prompt
        final_output = ""

        while True:
            agent = workflow.agents[current_agent_name]

            # Inject handoff tool for agents that have valid targets
            other_agents = [n for n in workflow.agents if n != current_agent_name]
            if other_agents:
                handoff_tool = make_handoff_tool(other_agents)
                # Shallow copy — do NOT mutate the original agent
                agent = dataclasses.replace(agent, tools=[*agent.tools, handoff_tool])

            append(self.db_path, Event(
                run_id=run_id,
                type=EventType.AGENT_STEP_STARTED,
                agent=current_agent_name,
                payload={"agent": current_agent_name},
            ))

            result = await self.runner.run_agent(
                run_id=run_id,
                agent=agent,
                prompt=current_prompt,
                client=client,
            )

            if result.handoff is not None:
                current_agent_name = result.handoff.target
                current_prompt = result.handoff.payload
            else:
                final_output = result.output
                break

        append(self.db_path, Event(
            run_id=run_id,
            type=EventType.RUN_FINISHED,
            payload={"output": final_output},
        ))

        return final_output
