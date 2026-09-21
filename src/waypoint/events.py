"""SQLite-backed event log for durable agent execution."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    AGENT_STEP_STARTED = "AGENT_STEP_STARTED"
    LLM_CALL_STARTED = "LLM_CALL_STARTED"
    LLM_CALL_COMMITTED = "LLM_CALL_COMMITTED"
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMMITTED = "TOOL_CALL_COMMITTED"
    HANDOFF_COMMITTED = "HANDOFF_COMMITTED"
    AGENT_STEP_FINISHED = "AGENT_STEP_FINISHED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_FAILED = "RUN_FAILED"


@dataclass
class Event:
    run_id: str
    type: EventType
    payload: dict
    agent: str | None = None
    ts: float = field(default_factory=time.time)
    id: int | None = None


def migrate(db_path: str) -> None:
    """Create DB file and tables if they don't exist. Safe to call multiple times."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  TEXT    NOT NULL,
                type    TEXT    NOT NULL,
                agent   TEXT,
                payload TEXT    NOT NULL DEFAULT '{}',
                ts      REAL    NOT NULL
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)
        """)
        con.commit()
    finally:
        con.close()


def append(db_path: str, event: Event) -> int:
    """Insert one event row and return the auto-incremented id."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        cur = con.execute(
            "INSERT INTO events (run_id, type, agent, payload, ts) VALUES (?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.type.value,
                event.agent,
                json.dumps(event.payload),
                event.ts,
            ),
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        con.close()


def list_events(db_path: str, run_id: str) -> list[Event]:
    """Return all events for a run in insertion order."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        rows = con.execute(
            "SELECT id, run_id, type, agent, payload, ts FROM events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    finally:
        con.close()

    result = []
    for row in rows:
        id_, run_id_, type_, agent, payload_str, ts = row
        result.append(
            Event(
                id=id_,
                run_id=run_id_,
                type=EventType(type_),
                agent=agent,
                payload=json.loads(payload_str),
                ts=ts,
            )
        )
    return result
