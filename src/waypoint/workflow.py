"""Workflow: a directed graph of Agent nodes with typed handoff edges."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic

from waypoint.agent import Agent
from waypoint.engine import DurableRunner
from waypoint.events import (
    Event,
    EventType,
    append,
    get_run_start_payload,
    list_events,
    list_running_runs,
)
from waypoint.tools import Tool

if TYPE_CHECKING:
    from waypoint.pubsub import EventBus


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
    def __init__(self, db_path: str, event_bus: "EventBus | None" = None) -> None:
        self.runner = DurableRunner(db_path, event_bus=event_bus)
        self.db_path = db_path
        self.event_bus = event_bus

    def _emit(self, event: Event) -> int:
        from waypoint.events import append as _append
        eid = _append(self.db_path, event)
        if self.event_bus is not None:
            event.id = eid
            self.event_bus.publish(event)
        return eid

    def _resume_state(
        self,
        run_id: str,
        workflow: Workflow,
        events: list[Event],
    ) -> tuple[str, str]:
        """Return (agent_name, prompt) to resume from.

        Scans for the last HANDOFF_COMMITTED; if none, returns
        (workflow.entry_point, original_prompt_from_RUN_STARTED).
        """
        original_prompt = ""
        last_handoff_target: str | None = None
        last_handoff_payload: str | None = None

        for event in events:
            if event.type == EventType.RUN_STARTED:
                original_prompt = event.payload.get("prompt", "")
            elif event.type == EventType.HANDOFF_COMMITTED:
                last_handoff_target = event.payload.get("target")
                last_handoff_payload = event.payload.get("payload", "")

        if last_handoff_target is not None:
            return last_handoff_target, last_handoff_payload or ""
        return workflow.entry_point, original_prompt

    async def run(
        self,
        run_id: str,
        workflow: Workflow,
        prompt: str,
        client: anthropic.AsyncAnthropic,
    ) -> str:
        # Load existing events once; reuse for both guard and resume-state inference
        existing_events = list_events(self.db_path, run_id)
        is_resuming = any(e.type == EventType.RUN_STARTED for e in existing_events)

        if not is_resuming:
            self._emit(Event(
                run_id=run_id,
                type=EventType.RUN_STARTED,
                payload={"workflow_name": workflow.name, "prompt": prompt},
            ))
        else:
            # Resuming: infer the correct entry point and prompt from the log
            current_agent_name, current_prompt = self._resume_state(run_id, workflow, existing_events)

        if not is_resuming:
            current_agent_name = workflow.entry_point
            current_prompt = prompt

        # Build set of agents whose AGENT_STEP_FINISHED already exists
        finished_agents: set[str] = set()
        for event in existing_events:
            if event.type == EventType.AGENT_STEP_FINISHED and event.agent:
                finished_agents.add(event.agent)

        final_output = ""

        while True:
            agent = workflow.agents[current_agent_name]

            # Skip agents that already completed in a previous run
            if current_agent_name in finished_agents:
                # Find the handoff that was committed for this agent, if any
                for event in existing_events:
                    if (
                        event.type == EventType.HANDOFF_COMMITTED
                        and event.agent == current_agent_name
                    ):
                        current_agent_name = event.payload["target"]
                        current_prompt = event.payload["payload"]
                        break
                else:
                    # Agent finished without handoff — run is already done
                    for event in reversed(existing_events):
                        if event.type == EventType.RUN_FINISHED:
                            return event.payload.get("output", "")
                    break
                continue

            # Inject handoff tool for agents that have valid targets
            other_agents = [n for n in workflow.agents if n != current_agent_name]
            if other_agents:
                handoff_tool = make_handoff_tool(other_agents)
                agent = dataclasses.replace(agent, tools=[*agent.tools, handoff_tool])

            self._emit(Event(
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

        self._emit(Event(
            run_id=run_id,
            type=EventType.RUN_FINISHED,
            payload={"output": final_output},
        ))

        return final_output


async def resume_runs(
    db_path: str,
    workflows: dict[str, Workflow],
    client: anthropic.AsyncAnthropic,
    event_bus: "EventBus | None" = None,
) -> list[str]:
    """Find all in-flight runs, resume each one.

    Returns list of run_ids that were resumed.
    Silently skips run_ids whose workflow_name is not in `workflows`.
    """
    running = list_running_runs(db_path)
    resumed: list[str] = []

    for run_id, workflow_name in running:
        workflow = workflows.get(workflow_name)
        if workflow is None:
            continue
        start_payload = get_run_start_payload(db_path, run_id)
        prompt = start_payload.get("prompt", "")
        runner = WorkflowRunner(db_path, event_bus=event_bus)
        await runner.run(run_id, workflow, prompt, client)
        resumed.append(run_id)

    return resumed
