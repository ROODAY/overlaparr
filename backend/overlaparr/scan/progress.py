"""Thread-safe fan-out of scan progress to subscribers (e.g. the SSE endpoint)."""

from __future__ import annotations

import queue
import threading
from typing import Any

PHASES = ("servers", "indexing", "instances", "matching", "reconcile", "hooks", "done")
QUEUE_SIZE = 1000


def _initial() -> dict[str, Any]:
    return {"phase": None, "detail": None, "done": 0, "total": 0, "running": False}


class ProgressBus:
    """Keeps the latest scan state; every publish merges into it and is fanned out.

    Each subscriber queue receives the full merged state after every publish. A full
    queue (a stalled subscriber) drops the event rather than blocking the scan.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _initial()
        self._subscribers: list[queue.Queue] = []

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._state.update(event)
            state = dict(self._state)
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(dict(state))
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)
