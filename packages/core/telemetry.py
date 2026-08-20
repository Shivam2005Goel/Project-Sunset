"""Tracing.

Two jobs, and the second one is the interesting one:

1. Ordinary observability - spans across the estate lifecycle, exported to Cloud Trace in
   cloud mode via OpenTelemetry.
2. Feeding the fiduciary record. A span here carries the *reasoning* attribute, not just
   timing, so the trace waterfall and the audit log tell the same story. See
   `packages/core/audit/`.

The in-memory recorder is always on. In local mode it is the whole implementation; in
cloud mode it runs alongside OTel so `make verify` can assert a span was produced without
round-tripping Cloud Trace.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from packages.core.clock import iso


@dataclass
class Span:
    name: str
    span_id: str
    trace_id: str
    parent_id: str | None
    started_at: str
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 3),
            "attributes": self.attributes,
            "status": self.status,
            "error": self.error,
        }


class SpanRecorder:
    """Collects finished spans in memory."""

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._lock = threading.Lock()

    def record(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)

    @property
    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def by_name(self, name: str) -> list[Span]:
        return [s for s in self.spans if s.name == name]

    def export(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([s.to_dict() for s in self.spans], indent=2),
            encoding="utf-8",
        )
        return path


_recorder = SpanRecorder()
_local = threading.local()


def recorder() -> SpanRecorder:
    return _recorder


def _current_context() -> tuple[str | None, str | None]:
    return getattr(_local, "trace_id", None), getattr(_local, "span_id", None)


def current_trace_id() -> str | None:
    return getattr(_local, "trace_id", None)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Open a span. Attributes are recorded even when the body raises."""
    parent_trace, parent_span = _current_context()
    current = Span(
        name=name,
        span_id=uuid.uuid4().hex[:16],
        trace_id=parent_trace or uuid.uuid4().hex,
        parent_id=parent_span,
        started_at=iso(),
        attributes={k: v for k, v in attributes.items() if v is not None},
    )
    _local.trace_id = current.trace_id
    _local.span_id = current.span_id
    started = time.perf_counter()
    otel_span = _start_otel(current)
    try:
        yield current
    except Exception as exc:  # noqa: BLE001 - re-raised below, we only annotate
        current.status = "ERROR"
        current.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        current.duration_ms = (time.perf_counter() - started) * 1000
        _recorder.record(current)
        _finish_otel(otel_span, current)
        _local.trace_id = parent_trace
        _local.span_id = parent_span


def annotate(span_obj: Span, **attributes: Any) -> None:
    span_obj.attributes.update({k: v for k, v in attributes.items() if v is not None})


# --- OpenTelemetry bridge -----------------------------------------------------------
# Optional import. Local mode has no OTel installed and must not care.

_otel_tracer = None
_otel_checked = False


def _tracer():  # pragma: no cover - exercised only in cloud mode
    global _otel_tracer, _otel_checked
    if _otel_checked:
        return _otel_tracer
    _otel_checked = True
    from packages.core.config import get_settings

    if not get_settings().is_cloud:
        return None
    try:
        from opentelemetry import trace

        _otel_tracer = trace.get_tracer("aftercare")
    except Exception:  # noqa: BLE001 - tracing must never break the estate
        _otel_tracer = None
    return _otel_tracer


def _start_otel(current: Span):  # pragma: no cover - cloud only
    tracer = _tracer()
    if tracer is None:
        return None
    try:
        ctx = tracer.start_span(current.name)
        for key, value in current.attributes.items():
            ctx.set_attribute(key, str(value))
        return ctx
    except Exception:  # noqa: BLE001
        return None


def _finish_otel(otel_span, current: Span) -> None:  # pragma: no cover - cloud only
    if otel_span is None:
        return
    try:
        for key, value in current.attributes.items():
            otel_span.set_attribute(key, str(value))
        if current.error:
            otel_span.set_attribute("error", current.error)
        otel_span.end()
    except Exception:  # noqa: BLE001
        pass


def configure_cloud_tracing() -> bool:  # pragma: no cover - cloud only
    """Wire OTel to Cloud Trace. Called by service entrypoints in cloud mode."""
    from packages.core.config import get_settings

    settings = get_settings()
    if not settings.is_cloud or not settings.project_id:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.project_id))
        )
        trace.set_tracer_provider(provider)
        return True
    except Exception:  # noqa: BLE001
        return False
