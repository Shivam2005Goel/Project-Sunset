"""The fiduciary audit log.

Append-only and hash-chained. Every record carries the digest of its predecessor, so
altering record 40 invalidates 41 through the end of the log. That is the difference
between "we don't delete rows" and "you can prove we didn't".

An executor can be sued. This file is the evidence.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from packages.core.clock import now
from packages.core.config import Settings, get_settings
from packages.core.models import AuditRecord
from packages.core.telemetry import current_trace_id

GENESIS = "0" * 64


class AuditSink:
    def append(self, record: AuditRecord) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read_all(self) -> list[AuditRecord]:  # pragma: no cover - interface
        raise NotImplementedError


class JsonlAuditSink(AuditSink):
    """Local + cloud mirror. One JSON object per line, never rewritten in place."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: AuditRecord) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), default=str) + "\n")

    def read_all(self) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(AuditRecord.model_validate(json.loads(line)))
        return records


class BigQueryAuditSink(AuditSink):  # pragma: no cover - cloud only
    """Append-only BigQuery sink - the query surface a lawyer's expert would use.

    Streaming inserts are append-only by construction, which is the property we want:
    there is no UPDATE path wired anywhere in this codebase.
    """

    def __init__(self, project_id: str, dataset: str = "aftercare_audit", table: str = "records") -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project_id)
        self._table = f"{project_id}.{dataset}.{table}"

    def append(self, record: AuditRecord) -> None:
        row = record.model_dump(mode="json")
        row["payload"] = json.dumps(row.get("payload", {}), default=str)
        errors = self._client.insert_rows_json(self._table, [row])
        if errors:
            raise RuntimeError(f"BigQuery audit insert failed: {errors}")

    def read_all(self) -> list[AuditRecord]:
        query = f"SELECT * FROM `{self._table}` ORDER BY seq"  # noqa: S608 - table id is not user input
        rows = []
        for row in self._client.query(query).result():
            data = dict(row)
            data["payload"] = json.loads(data.get("payload") or "{}")
            rows.append(AuditRecord.model_validate(data))
        return rows


class TeeSink(AuditSink):
    """Write to several sinks. Cloud mode keeps the JSONL mirror so that a BigQuery
    outage cannot silently create a gap in the fiduciary record."""

    def __init__(self, *sinks: AuditSink) -> None:
        self._sinks = [s for s in sinks if s is not None]

    def append(self, record: AuditRecord) -> None:
        for sink in self._sinks:
            sink.append(record)

    def read_all(self) -> list[AuditRecord]:
        return self._sinks[0].read_all() if self._sinks else []


class AuditLog:
    """The only way to write an audit record."""

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._lock = threading.Lock()
        existing = sink.read_all()
        self._seq = existing[-1].seq if existing else 0
        self._prev = existing[-1].digest if existing else GENESIS

    def record(
        self,
        *,
        estate_id: str,
        action: str,
        reasoning: str,
        actor: str = "system",
        institution_id: str | None = None,
        case_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditRecord:
        with self._lock:
            entry = AuditRecord(
                seq=self._seq + 1,
                at=now(),
                estate_id=estate_id,
                institution_id=institution_id,
                case_id=case_id,
                actor=actor,
                action=action,
                reasoning=reasoning,
                payload=payload or {},
                trace_id=current_trace_id(),
                prev_digest=self._prev,
            )
            entry.digest = entry.compute_digest()
            self._sink.append(entry)
            self._seq = entry.seq
            self._prev = entry.digest
            return entry

    def all(self) -> list[AuditRecord]:
        return self._sink.read_all()

    def for_estate(self, estate_id: str) -> list[AuditRecord]:
        return [r for r in self.all() if r.estate_id == estate_id]

    def for_case(self, case_id: str) -> list[AuditRecord]:
        return [r for r in self.all() if r.case_id == case_id]

    def verify(self) -> tuple[bool, str | None]:
        """Walk the chain. Returns (ok, first_broken_record_id)."""
        prev = GENESIS
        for index, record in enumerate(self.all(), start=1):
            if record.seq != index:
                return False, record.id
            if record.prev_digest != prev:
                return False, record.id
            if record.compute_digest() != record.digest:
                return False, record.id
            prev = record.digest
        return True, None


_log: AuditLog | None = None
_log_lock = threading.Lock()


def build_sink(settings: Settings | None = None) -> AuditSink:
    settings = settings or get_settings()
    jsonl = JsonlAuditSink(settings.audit_path)
    if settings.is_cloud and settings.project_id:  # pragma: no cover - cloud only
        try:
            return TeeSink(jsonl, BigQueryAuditSink(settings.project_id))
        except Exception:  # noqa: BLE001 - never lose the local record over a cloud fault
            return jsonl
    return jsonl


def get_audit_log() -> AuditLog:
    global _log
    if _log is not None:
        return _log
    with _log_lock:
        if _log is None:
            _log = AuditLog(build_sink())
    return _log


def set_audit_log(log: AuditLog | None) -> None:
    global _log
    with _log_lock:
        _log = log
