"""Runtime adapter - where a sub-agent's work actually executes.

The property that matters is dormancy: an institution sub-agent waits two weeks for a
bank to reply and must hold no CPU while it waits. Agent Runtime gives that natively;
Cloud Run Jobs plus Cloud Tasks gives the same shape - a scheduled wake-up that
rehydrates state from the store and runs one turn.

Local mode runs the same handler in-process with an explicit wake queue, so the *number
of turns* and *the order they run in* are identical to cloud. Only the scheduler differs.
"""

from __future__ import annotations

import heapq
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from packages.core.clock import now
from packages.core.config import Settings, get_settings
from packages.core.logging import get_logger

log = get_logger("adapter.runtime")

WakeHandler = Callable[["AgentHandle", dict[str, Any]], Any]


@dataclass
class AgentSpec:
    """What a sub-agent is, independent of where it runs."""

    agent_id: str
    kind: str  # "institution" | "root"
    estate_id: str
    institution_id: str | None = None
    handler: str = "institution_agent"
    # Agent Identity: one narrowly-scoped service account per institution sub-agent, so a
    # compromised utility agent cannot reach brokerage credentials.
    service_account: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(order=True)
class _ScheduledWake:
    when: datetime
    seq: int
    agent_id: str = field(compare=False)
    event: dict[str, Any] = field(compare=False, default_factory=dict)


@dataclass
class AgentHandle:
    agent_id: str
    spec: AgentSpec
    dormant: bool = True
    turns: int = 0
    last_woken_at: datetime | None = None


class RuntimeAdapter(ABC):
    name = "runtime"

    def __init__(self) -> None:
        self._handlers: dict[str, WakeHandler] = {}

    def register_handler(self, name: str, handler: WakeHandler) -> None:
        self._handlers[name] = handler

    @abstractmethod
    def spawn(self, spec: AgentSpec) -> AgentHandle: ...

    @abstractmethod
    def wake(self, agent_id: str, event: dict[str, Any]) -> Any: ...

    @abstractmethod
    def schedule(self, agent_id: str, when: datetime, event: dict[str, Any]) -> None: ...

    @abstractmethod
    def handles(self) -> list[AgentHandle]: ...

    def dormant_count(self) -> int:
        return sum(1 for handle in self.handles() if handle.dormant)


class InProcessRuntime(RuntimeAdapter):
    """Local fallback: a wake queue plus a handler table.

    `run_due(moment)` is what the time-warp driver calls after advancing the clock. It is
    the local stand-in for Cloud Tasks firing a scheduled HTTP request at a Cloud Run Job.
    """

    name = "cloud_run_jobs"

    def __init__(self) -> None:
        super().__init__()
        self._agents: dict[str, AgentHandle] = {}
        self._queue: list[_ScheduledWake] = []
        self._seq = 0
        self._lock = threading.Lock()

    def spawn(self, spec: AgentSpec) -> AgentHandle:
        with self._lock:
            handle = AgentHandle(agent_id=spec.agent_id, spec=spec)
            self._agents[spec.agent_id] = handle
        log.info(
            "agent.spawned",
            agent_id=spec.agent_id,
            kind=spec.kind,
            service_account=spec.service_account,
        )
        return handle

    def wake(self, agent_id: str, event: dict[str, Any]) -> Any:
        handle = self._agents.get(agent_id)
        if handle is None:
            raise KeyError(f"no agent '{agent_id}' on this runtime")
        handler = self._handlers.get(handle.spec.handler)
        if handler is None:
            raise LookupError(
                f"no handler registered for '{handle.spec.handler}'. "
                f"services/worker registers it at import time."
            )
        handle.dormant = False
        handle.turns += 1
        handle.last_woken_at = now()
        try:
            return handler(handle, event)
        finally:
            handle.dormant = True

    def schedule(self, agent_id: str, when: datetime, event: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            heapq.heappush(self._queue, _ScheduledWake(when, self._seq, agent_id, event))

    def due(self, moment: datetime) -> list[_ScheduledWake]:
        ready: list[_ScheduledWake] = []
        with self._lock:
            while self._queue and self._queue[0].when <= moment:
                ready.append(heapq.heappop(self._queue))
        return ready

    def run_due(self, moment: datetime) -> int:
        """Fire every wake-up that has come due. Returns the number of turns run."""
        turns = 0
        for item in self.due(moment):
            if item.agent_id not in self._agents:
                continue
            self.wake(item.agent_id, item.event)
            turns += 1
        return turns

    def handles(self) -> list[AgentHandle]:
        return list(self._agents.values())

    def pending_wakes(self) -> int:
        with self._lock:
            return len(self._queue)


class CloudTasksRuntime(InProcessRuntime):  # pragma: no cover - cloud only
    """Cloud Run Jobs + Cloud Tasks.

    Spawning is bookkeeping - there is no long-lived process to create, which is exactly
    the point. `schedule` enqueues an HTTP task; the Cloud Run Job that receives it calls
    `wake` locally after rehydrating the case from Firestore.
    """

    name = "cloud_run_jobs_cloud"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        from google.cloud import tasks_v2

        self._client = tasks_v2.CloudTasksClient()
        self._settings = settings
        self._parent = self._client.queue_path(
            settings.project_id, settings.region, "aftercare-wakeups"
        )
        self._target = f"https://aftercare-worker-{settings.project_id}.run.app/wake"

    def schedule(self, agent_id: str, when: datetime, event: dict[str, Any]) -> None:
        import json

        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        schedule_time = timestamp_pb2.Timestamp()
        schedule_time.FromDatetime(when)
        task = tasks_v2.Task(
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=self._target,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"agent_id": agent_id, "event": event}).encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=f"aftercare-worker@{self._settings.project_id}.iam.gserviceaccount.com"
                ),
            ),
            schedule_time=schedule_time,
        )
        self._client.create_task(parent=self._parent, task=task)


class AgentRuntimeAdapter(InProcessRuntime):  # pragma: no cover - GEAP, unverified
    """Google Agent Runtime. Unverified against the live service - see CLAUDE.md."""

    name = "agent_runtime"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        raise RuntimeError(
            "Agent Runtime client is not wired yet - confirm GA status on Day 0. Set "
            "RUNTIME_ADAPTER=cloud_run_jobs meanwhile; it satisfies the same contract."
        )


_adapter: RuntimeAdapter | None = None


def get_runtime_adapter(settings: Settings | None = None) -> RuntimeAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter
    settings = settings or get_settings()
    choice = settings.effective_adapter("runtime")

    if choice == "agent_runtime":  # pragma: no cover - cloud only
        try:
            _adapter = AgentRuntimeAdapter(settings)
            return _adapter
        except RuntimeError as exc:
            log.warning("runtime.fallback", reason=str(exc))
            choice = "cloud_run_jobs"

    if choice == "cloud_run_jobs" and settings.is_cloud and settings.project_id:  # pragma: no cover
        _adapter = CloudTasksRuntime(settings)
    else:
        _adapter = InProcessRuntime()
    return _adapter


def set_runtime_adapter(adapter: RuntimeAdapter | None) -> None:
    global _adapter
    _adapter = adapter


def service_account_for(institution_id: str, project_id: str | None) -> str:
    """One scoped identity per institution sub-agent.

    Terraform provisions the role bindings (`infra/iam.tf`); this is the naming contract
    both sides agree on.
    """
    slug = institution_id.replace("_", "-")[:24].strip("-")
    domain = f"{project_id}.iam.gserviceaccount.com" if project_id else "local.invalid"
    return f"agent-{slug}@{domain}"
