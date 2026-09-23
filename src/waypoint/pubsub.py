"""In-process event bus backed by asyncio.Queue."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waypoint.events import Event


class EventBus:
    """Fan-out pub/sub for run events. All methods are called inside the asyncio loop."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Return a new Queue that will receive all future events for run_id."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        try:
            self._subscribers[run_id].remove(q)
        except ValueError:
            pass

    def publish(self, event: "Event") -> None:
        """Put event into every queue subscribed for event.run_id."""
        for q in self._subscribers.get(event.run_id, []):
            q.put_nowait(event)

    def close_run(self, run_id: str) -> None:
        """Push None sentinel to all subscribers, then drop them."""
        for q in self._subscribers.get(run_id, []):
            q.put_nowait(None)
        self._subscribers.pop(run_id, None)
