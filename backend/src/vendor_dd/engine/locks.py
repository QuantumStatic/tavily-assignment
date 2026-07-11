from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class KeyedLocks:
    """Process-wide per-key mutexes. Callers that share a key run one-at-a-time; callers
    with different keys never block each other.

    Used to single-flight report generation by resolved DOMAIN: two vendor rows whose
    names resolve to the same domain would otherwise each run the full (expensive) Tavily
    + LLM section pass and race each other's writes to the shared, domain-keyed cache.
    Serializing them means the first does the work and the second finds it all cached.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def acquire(self, key: str) -> Iterator[None]:
        lock = self._lock_for(key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
