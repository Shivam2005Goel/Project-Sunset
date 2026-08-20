"""Test fixtures.

Every test runs against an isolated data directory, so a test run can never read or
corrupt the state left behind by `python tasks.py seed`. The environment is set before
any Aftercare module is imported, because `get_settings()` is cached for the process.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aftercare-tests-"))
os.environ["AFTERCARE_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["AFTERCARE_MODE"] = "local"
os.environ.pop("GEMINI_API_KEY", None)

import pytest  # noqa: E402

from packages.core.adapters import reset_all_adapters  # noqa: E402
from packages.core.audit.sink import set_audit_log  # noqa: E402
from packages.core.clock import SimulatedClock, reset_clock, set_clock  # noqa: E402
from packages.core.config import get_settings, reset_settings_cache  # noqa: E402
from packages.core.llm import set_llm  # noqa: E402
from packages.core.logging import set_level  # noqa: E402
from packages.core.store import set_store  # noqa: E402
from packages.core.telemetry import recorder  # noqa: E402

set_level("ERROR")


def _reset_singletons() -> None:
    set_store(None)
    set_audit_log(None)
    set_llm(None)
    reset_all_adapters()
    recorder().clear()

    from services.api.approvals import set_approval_service
    from services.api.transport import set_transport
    from services.inbox.handler import set_pipeline
    from services.orchestrator.root import set_orchestrator
    from services.worker.agent import set_agent

    set_approval_service(None)
    set_transport(None)
    set_pipeline(None)
    set_orchestrator(None)
    set_agent(None)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Give each test its own data directory and a clean set of singletons."""
    monkeypatch.setenv("AFTERCARE_DATA_DIR", str(tmp_path / "aftercare"))
    reset_settings_cache()
    get_settings().ensure_dirs()
    _reset_singletons()
    yield
    _reset_singletons()
    reset_clock()
    reset_settings_cache()


@pytest.fixture
def frozen_clock():
    """A simulated clock at a fixed moment, so timestamps are deterministic."""
    from datetime import datetime, timezone

    clock = SimulatedClock(datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc))
    previous = set_clock(clock)
    yield clock
    set_clock(previous)


@pytest.fixture
def seeded_estate(frozen_clock):
    """A fully seeded estate: 23 obligations, 23 cases, 23 drafts awaiting approval."""
    from demo.seed import seed

    result = seed(fresh=False, quiet=True)
    return result


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session_dir():
    yield
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
