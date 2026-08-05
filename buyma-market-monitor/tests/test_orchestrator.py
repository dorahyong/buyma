import threading
from orchestrator import Stage, run_cycle


class _FakeClock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


class _FakeCB:
    def __init__(self):
        self._open = False
    def is_open(self):
        return self._open
    def force_open(self):
        self._open = True


def test_run_cycle_runs_stages_in_order_then_revisit():
    clock = _FakeClock()
    calls = []
    def mk(name, dt):
        def fn(deadline, cb, stop_event):
            calls.append(name)
            clock.advance(dt)
            return f"{name}-done"
        return fn
    stages = [
        Stage("sellers", mk("sellers", 100), cap_seconds=3600),
        Stage("orders", mk("orders", 200), cap_seconds=3600),
        Stage("exposure", mk("exposure", 150), cap_seconds=3600),
        Stage("scan", mk("scan", 300), cap_seconds=3600),
    ]
    revisit_seen = {}
    def revisit_fn(remaining_seconds, cb, stop_event):
        revisit_seen["remaining"] = remaining_seconds
        clock.advance(remaining_seconds)
    run_cycle(stages=stages, revisit_fn=revisit_fn, cycle_seconds=86400,
              cooldown_seconds=1, clock=clock, sleep_fn=lambda s: None,
              make_cb=lambda: _FakeCB(), stop_event=threading.Event())
    assert calls == ["sellers", "orders", "exposure", "scan"]
    # 86400 - (100+200+150+300) = 85650
    assert revisit_seen["remaining"] == 85650


def test_run_cycle_cooldown_and_retry_on_block():
    clock = _FakeClock()
    attempts = []
    def make_cb():
        return _FakeCB()
    def sellers_fn(deadline, cb, stop_event):
        attempts.append("sellers")
        if len(attempts) == 1:
            cb.force_open()
    slept = []
    stages = [Stage("sellers", sellers_fn, cap_seconds=3600)]
    run_cycle(stages=stages, revisit_fn=lambda rem, cb, se: None,
              cycle_seconds=86400, cooldown_seconds=42,
              clock=clock, sleep_fn=lambda s: slept.append(s),
              make_cb=make_cb, stop_event=threading.Event())
    assert attempts == ["sellers", "sellers"]
    assert slept == [42]


def test_run_cycle_stops_on_stop_event():
    clock = _FakeClock()
    ev = threading.Event(); ev.set()
    calls = []
    stages = [Stage("sellers", lambda d, cb, se: calls.append("x"), cap_seconds=10)]
    run_cycle(stages=stages, revisit_fn=lambda rem, cb, se: calls.append("revisit"),
              cycle_seconds=86400, cooldown_seconds=1,
              clock=clock, sleep_fn=lambda s: None,
              make_cb=lambda: _FakeCB(), stop_event=ev)
    assert calls == []
