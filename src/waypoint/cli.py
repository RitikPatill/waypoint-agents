from __future__ import annotations

from typing import Optional

import typer

from waypoint import __version__

app = typer.Typer(help="Waypoint — durable multi-agent orchestrator.")


@app.command()
def version() -> None:
    """Print the current Waypoint version."""
    typer.echo(f"waypoint {__version__}")


@app.command()
def serve(
    db_path: str = typer.Option("waypoint.db", help="SQLite event log path"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    dev: bool = typer.Option(False, "--dev", help="Enable /debug/kill-worker endpoint"),
    kill_after: Optional[int] = typer.Option(
        None, help="Kill the process after N seconds (for crash-resume testing)"
    ),
) -> None:
    """Start Waypoint, resuming any interrupted runs, and serve the HTTP API."""
    import uvicorn

    from waypoint.api import create_app

    fastapi_app = create_app(db_path=db_path, dev_mode=dev, kill_after=kill_after)
    typer.echo(f"Starting Waypoint on http://{host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port)
