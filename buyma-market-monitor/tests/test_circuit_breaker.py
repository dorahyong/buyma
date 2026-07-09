import threading

import pytest

from crawler.circuit_breaker import CircuitBreaker, CircuitOpenError


class FakeClock:
    """Manually advanced monotonic clock for deterministic tests."""
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_is_open_false_before_any_blocks():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    assert cb.is_open() is False


def test_is_open_false_below_threshold():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is False


def test_is_open_true_at_threshold():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is True


def test_circuit_latches_open_after_window_expires():
    """Once tripped, stays tripped even if old events would have aged out."""
    clock = FakeClock()
    cb = CircuitBreaker(threshold=2, window_seconds=10, clock=clock)
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is True
    clock.advance(100)  # well past window
    assert cb.is_open() is True


def test_old_events_pruned_before_threshold_reached():
    """Blocks outside the window do not count toward the threshold."""
    clock = FakeClock()
    cb = CircuitBreaker(threshold=3, window_seconds=10, clock=clock)
    cb.record_block()
    clock.advance(11)  # first event ages out
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is False  # only 2 events within window


def test_assert_not_open_raises_when_tripped():
    cb = CircuitBreaker(threshold=1, window_seconds=60, clock=FakeClock())
    cb.record_block()
    with pytest.raises(CircuitOpenError):
        cb.assert_not_open()


def test_assert_not_open_passes_when_closed():
    cb = CircuitBreaker(threshold=2, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.assert_not_open()  # must not raise


def test_circuit_open_error_carries_last_status_none():
    """Compatible with on_error callback convention: getattr(e, 'last_status', None)."""
    cb = CircuitBreaker(threshold=1, window_seconds=60, clock=FakeClock())
    cb.record_block()
    try:
        cb.assert_not_open()
    except CircuitOpenError as e:
        assert getattr(e, "last_status", "missing") is None


def test_force_open_latches_open():
    cb = CircuitBreaker(threshold=5, window_seconds=60)
    assert not cb.is_open()
    cb.force_open()
    assert cb.is_open()


def test_thread_safe_concurrent_record_block():
    """50 threads each call record_block once — final count of events must be 50."""
    cb = CircuitBreaker(threshold=10_000, window_seconds=60, clock=FakeClock())

    def worker():
        cb.record_block()

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Internal _events list should have exactly 50 entries
    assert len(cb._events) == 50
