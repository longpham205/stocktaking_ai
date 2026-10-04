"""Temporary lockout after repeated wrong passwords, per (account, client IP). In memory: the API
is one process, and a restart forgiving the count is acceptable."""

import time
from collections.abc import Callable

Key = tuple[str, str]


class LoginLimiter:
    def __init__(self, max_failed: int, lockout_seconds: int, clock: Callable[[], float] = time.time):
        self.max_failed, self.lockout = max_failed, lockout_seconds
        self._clock = clock
        self._failures: dict[Key, list[float]] = {}

    def retry_after(self, key: Key) -> int:
        """Seconds the pair stays locked; 0 when it may try."""
        now = self._clock()
        failures = [t for t in self._failures.get(key, []) if now - t < self.lockout]
        if failures:
            self._failures[key] = failures
        else:
            self._failures.pop(key, None)
        if len(failures) >= self.max_failed:
            return int(self.lockout - (now - failures[0])) + 1
        return 0

    def fail(self, key: Key) -> None:
        self._failures.setdefault(key, []).append(self._clock())

    def reset(self, key: Key) -> None:
        self._failures.pop(key, None)
