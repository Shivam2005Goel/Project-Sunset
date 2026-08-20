"""The clock.

Application code never calls `datetime.now()`. It asks the clock. That single discipline
is what makes the time-warp demo real: `demo/timewarp.py` swaps in a `SimulatedClock`,
and six weeks of estate administration run through the *production* code paths in four
minutes without a single `if demo_mode` branch anywhere.

`tests/test_policy.py` fails the build if `datetime.now()` or `datetime.utcnow()` appears
outside this module.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall time. Used in production and in tests that do not care about time."""

    kind = "system"

    def now(self) -> datetime:
        return datetime.now(timezone.utc)  # noqa: DTZ005 - the one legitimate call site

    def __repr__(self) -> str:
        return "SystemClock()"


class SimulatedClock:
    """A clock the demo can wind forward.

    `factor` is cosmetic bookkeeping for the UI ("400x"); the driver advances this clock
    explicitly rather than sleeping, because a demo that actually slept for six weeks
    would be a worse demo.
    """

    kind = "simulated"

    def __init__(self, start: datetime, factor: int = 1) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._current = start
        self.start = start
        self.factor = factor
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._current

    def advance(self, delta: timedelta) -> datetime:
        with self._lock:
            self._current = self._current + delta
            return self._current

    def advance_to(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        with self._lock:
            if moment > self._current:
                self._current = moment
            return self._current

    @property
    def elapsed(self) -> timedelta:
        return self.now() - self.start

    def __repr__(self) -> str:
        return f"SimulatedClock(now={self._current.isoformat()}, factor={self.factor})"


_clock: Clock = SystemClock()
_clock_lock = threading.Lock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    """Install a clock. Returns the previous one so callers can restore it."""
    global _clock
    with _clock_lock:
        previous = _clock
        _clock = clock
    return previous


def now() -> datetime:
    return _clock.now()


def iso() -> str:
    return _clock.now().isoformat()


def reset_clock() -> None:
    set_clock(SystemClock())
