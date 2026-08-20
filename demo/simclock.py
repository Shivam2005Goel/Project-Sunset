"""Simulated-clock state, persisted between processes.

`make seed` and `make demo` are separate commands and therefore separate processes, but
they share one simulated calendar: seeding happens the day after the death, and the
time-warp picks up from wherever the last run left off. Without this file the API would
have no idea what "today" means to the estate, and the dashboard clock would show wall
time over a six-week case history - which is exactly the kind of small incoherence a
judge notices.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.core.clock import SimulatedClock, set_clock
from packages.core.config import get_settings
from demo.estate import DEATH_DATE


def state_path() -> Path:
    return get_settings().data_dir / "simclock.json"


def start_moment() -> datetime:
    """Day zero: the morning after the death, when the executor first sits down."""
    death = datetime.fromisoformat(DEATH_DATE).replace(tzinfo=timezone.utc)
    return death + timedelta(days=1, hours=9)


def save(clock: SimulatedClock) -> Path:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "start": clock.start.isoformat(),
                "now": clock.now().isoformat(),
                "factor": clock.factor,
                "elapsed_days": clock.elapsed.days,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load() -> dict | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def install(*, fresh: bool = False, factor: int | None = None) -> SimulatedClock:
    """Install the simulated clock globally and return it.

    `fresh=True` restarts the calendar at day zero; otherwise it resumes.
    """
    settings = get_settings()
    factor = factor or max(1, settings.timewarp_factor)

    saved = None if fresh else load()
    start = datetime.fromisoformat(saved["start"]) if saved else start_moment()
    clock = SimulatedClock(start=start, factor=factor)
    if saved:
        clock.advance_to(datetime.fromisoformat(saved["now"]))

    set_clock(clock)
    save(clock)
    return clock
