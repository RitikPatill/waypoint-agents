import typer

from waypoint import __version__

app = typer.Typer(help="Waypoint — durable multi-agent orchestrator.")


@app.command()
def version() -> None:
    """Print the current Waypoint version."""
    typer.echo(f"waypoint {__version__}")
