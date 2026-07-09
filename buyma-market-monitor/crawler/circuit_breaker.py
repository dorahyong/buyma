"""Sliding-window circuit breaker for stopping HTTP fetches after repeated blocks.

Used to halt the monitoring run when BUYMA starts returning 403/429 — continuing
to hammer the server worsens our IP reputation and wastes the run's budget.

Latching design: once the threshold is reached within the window, the circuit
stays open until the process restarts. There is no half-open / probe state.
Operator restarts the run after investigating the cause.
"""
import threading
import time
from typing import Callable


class CircuitOpenError(Exception):
    """Raised when a request is attempted while the circuit is open."""

    def __init__(self, blocks: int, threshold: int, window_seconds: int):
        self.last_status: int | None = None
        super().__init__(
            f"circuit breaker open: {blocks} blocks in {window_seconds}s "
            f"window (threshold={threshold})"
        )


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 5,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: list[float] = []
        self._tripped = False
        self._lock = threading.Lock()

    def record_block(self) -> None:
        """Record a 403/429 observation. May trip the circuit."""
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            self._events = [t for t in self._events if t > cutoff]
            self._events.append(now)
            if len(self._events) >= self.threshold:
                self._tripped = True

    def is_open(self) -> bool:
        with self._lock:
            return self._tripped

    def force_open(self) -> None:
        """Latch the breaker open immediately, without requiring threshold events.

        Used when an external subsystem (e.g. orders crawler) returns a definitive
        IP-block signal so the orchestrator's cooldown logic fires correctly.
        """
        with self._lock:
            self._tripped = True

    def assert_not_open(self) -> None:
        with self._lock:
            if self._tripped:
                raise CircuitOpenError(
                    blocks=len(self._events),
                    threshold=self.threshold,
                    window_seconds=self.window_seconds,
                )
