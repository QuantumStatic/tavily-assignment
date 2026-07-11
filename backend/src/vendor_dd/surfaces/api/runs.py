from __future__ import annotations

import queue
import threading

DONE = object()   # terminal sentinel every subscriber receives exactly once


class ReportRun:
    """Fan-out for one in-flight report generation: the generator thread publishes
    events, any number of SSE subscribers tail them. A subscriber that joins late
    gets the full history replayed first, so a second browser tab sees the report
    from the beginning instead of only the remaining frames."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: list[object] = []
        self._subscribers: list[queue.Queue] = []
        self._done = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for ev in self._history:
                q.put(ev)
            if self._done:
                q.put(DONE)
            else:
                self._subscribers.append(q)
        return q

    def publish(self, ev: object) -> None:
        with self._lock:
            self._history.append(ev)
            for q in self._subscribers:
                q.put(ev)

    def finish(self) -> None:
        with self._lock:
            self._done = True
            for q in self._subscribers:
                q.put(DONE)
            self._subscribers.clear()


class RunRegistry:
    """At most one live generation per vendor. get_or_create is atomic: the caller
    that gets created=True owns starting the worker thread; everyone else tails."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[int, ReportRun] = {}

    def get_or_create(self, vendor_id: int) -> tuple[ReportRun, bool]:
        with self._lock:
            run = self._runs.get(vendor_id)
            if run is not None:
                return run, False
            run = ReportRun()
            self._runs[vendor_id] = run
            return run, True

    def remove(self, vendor_id: int) -> None:
        with self._lock:
            self._runs.pop(vendor_id, None)
