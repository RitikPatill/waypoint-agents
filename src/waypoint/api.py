"""FastAPI application factory for Waypoint."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from waypoint.events import Event, EventType, list_events, migrate
from waypoint.pubsub import EventBus
from waypoint.workflow import WorkflowRunner, resume_runs
from waypoint.workflows import BUILTIN_WORKFLOWS


class RunRequest(BaseModel):
    workflow: str
    prompt: str


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "type": event.type.value,
        "agent": event.agent,
        "payload": event.payload,
        "ts": event.ts,
    }


def create_app(
    db_path: str = "waypoint.db",
    dev_mode: bool = False,
    kill_after: int | None = None,
) -> FastAPI:
    # Migrate synchronously at creation time so the DB is ready even when
    # the lifespan is not triggered (e.g., in ASGI test clients).
    migrate(db_path)

    bus = EventBus()
    client = anthropic.AsyncAnthropic()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate(db_path)  # no-op if already migrated; ensures resume runs after schema ready
        if kill_after is not None:
            loop = asyncio.get_event_loop()
            loop.call_later(kill_after, lambda: os.kill(os.getpid(), signal.SIGTERM))
        asyncio.create_task(resume_runs(db_path, BUILTIN_WORKFLOWS, client, event_bus=bus))
        yield

    app = FastAPI(title="Waypoint", lifespan=lifespan)
    app.state.bus = bus
    app.state.client = client
    app.state.db_path = db_path
    app.state.dev_mode = dev_mode

    async def _run_workflow(run_id: str, workflow_name: str, prompt: str) -> None:
        workflow = BUILTIN_WORKFLOWS[workflow_name]
        try:
            runner = WorkflowRunner(db_path, event_bus=bus)
            await runner.run(run_id, workflow, prompt, client)
        finally:
            bus.close_run(run_id)

    @app.get("/")
    async def index():
        html = (Path(__file__).parent / "static" / "index.html").read_text()
        return HTMLResponse(html)

    @app.get("/workflows")
    async def list_workflows():
        return {"workflows": list(BUILTIN_WORKFLOWS.keys())}

    @app.post("/runs", status_code=202)
    async def create_run(req: RunRequest):
        if req.workflow not in BUILTIN_WORKFLOWS:
            raise HTTPException(status_code=404, detail=f"Unknown workflow: {req.workflow}")
        run_id = str(uuid.uuid4())
        asyncio.create_task(_run_workflow(run_id, req.workflow, req.prompt))
        return {"run_id": run_id}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        events = list_events(db_path, run_id)
        if not events:
            raise HTTPException(status_code=404, detail="Run not found")

        status = "running"
        workflow = ""
        for event in events:
            if event.type == EventType.RUN_STARTED:
                workflow = event.payload.get("workflow_name", "")
            elif event.type == EventType.RUN_FINISHED:
                status = "finished"
            elif event.type == EventType.RUN_FAILED:
                status = "failed"

        return {
            "run_id": run_id,
            "status": status,
            "workflow": workflow,
            "event_count": len(events),
        }

    @app.get("/runs/{run_id}/events")
    async def stream_events(run_id: str):
        async def generate():
            # 1. Subscribe first so close_run's sentinel is never missed (TOCTOU fix)
            q = bus.subscribe(run_id)
            try:
                # 2. Replay all historical events (reconnect safety)
                history = list_events(db_path, run_id)
                seen_ids = set()
                for event in history:
                    seen_ids.add(event.id)
                    yield f"data: {json.dumps(event_to_dict(event))}\n\n"

                # 3. If already finished, no need to stream from bus
                if history and history[-1].type in (EventType.RUN_FINISHED, EventType.RUN_FAILED):
                    return

                # 4. Stream live events, skipping any already replayed from history
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=25)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break  # sentinel from close_run
                    if event.id not in seen_ids:
                        yield f"data: {json.dumps(event_to_dict(event))}\n\n"
            finally:
                bus.unsubscribe(run_id, q)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/debug/kill-worker")
    async def kill_worker():
        # Read env at request time so tests can override regardless of create_app args
        is_dev = dev_mode or os.environ.get("WAYPOINT_DEV") == "1"
        if not is_dev:
            raise HTTPException(status_code=403, detail="Only available in dev mode")
        os.kill(os.getpid(), signal.SIGTERM)
        return {"ok": True}

    return app
