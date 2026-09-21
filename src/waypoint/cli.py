from __future__ import annotations

import asyncio
import os
import signal
from typing import Optional

import anthropic
import typer

from waypoint import __version__
from waypoint.events import list_running_runs, migrate
from waypoint.workflow import resume_runs

app = typer.Typer(help="Waypoint — durable multi-agent orchestrator.")


@app.command()
def version() -> None:
    """Print the current Waypoint version."""
    typer.echo(f"waypoint {__version__}")


@app.command()
def serve(
    db_path: str = typer.Option("waypoint.db", help="SQLite event log path"),
    kill_after: Optional[int] = typer.Option(
        None, help="Kill the process after N seconds (for crash-resume testing)"
    ),
) -> None:
    """Start Waypoint, resuming any interrupted runs."""
    migrate(db_path)

    running = list_running_runs(db_path)
    if running:
        typer.echo(f"Found {len(running)} interrupted run(s) to resume:")
        for run_id, workflow_name in running:
            typer.echo(f"  • {run_id} (workflow: {workflow_name})")
    else:
        typer.echo("No interrupted runs found.")

    # Workflow registration is M6's job; for M5 we resume only what's provided
    workflows: dict = {}

    client = anthropic.AsyncAnthropic()

    async def _run() -> None:
        if kill_after is not None:
            _sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            loop = asyncio.get_event_loop()
            loop.call_later(kill_after, lambda: os.kill(os.getpid(), _sig))

        resumed = await resume_runs(db_path, workflows, client)
        if resumed:
            typer.echo(f"Resumed {len(resumed)} run(s): {resumed}")
        else:
            typer.echo("Nothing to resume.")

    asyncio.run(_run())
