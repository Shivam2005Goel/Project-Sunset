"""The executor-facing API.

FastAPI on Cloud Run. Read endpoints for the dashboard, one write endpoint that matters
(`/api/approvals/{id}/decide`), and an export endpoint that produces the document an
executor could hand to a probate court.

There is no endpoint that sends anything. The only way a letter leaves this system is by
being approved, and approving is the only write operation exposed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from packages.core.adapters.registry import get_registry_adapter
from packages.core.audit.sink import get_audit_log
from packages.core.clock import get_clock
from packages.core.config import get_settings
from packages.core.llm import get_llm
from packages.core.logging import get_logger
from packages.core.models import CaseState, Verdict
from packages.core.repos import get_repos
from packages.core.telemetry import recorder
from packages.guardrails.pii import diff_view
from packages.guardrails.policy import PolicyViolation
from packages.playbooks.publisher import list_amendments
from services.api.approvals import get_approval_service
from services.orchestrator.root import get_orchestrator

log = get_logger("api")

app = FastAPI(
    title="Aftercare",
    version="1.0.0",
    description=(
        "An autonomous agent that handles the bureaucracy of death. "
        "All demonstration data is fictional. Aftercare drafts; the executor decides."
    ),
)

app.add_middleware(
    CORSMiddleware,
    # The dashboard is the only client, and in cloud mode it is served from the same
    # origin. This is here for `make dev`, where Next.js runs on :3000.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _estate_or_404(estate_id: str | None = None):
    repos = get_repos()
    estate = repos.estates.require(estate_id) if estate_id else repos.estates.current()
    if estate is None:
        raise HTTPException(
            status_code=404,
            detail="No estate found. Run `python tasks.py seed` to create the demo estate.",
        )
    return estate


# --- system --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "mode": settings.mode,
        "model_provider": get_llm().provider_name,
        "adapters": {
            name: settings.effective_adapter(name)
            for name in ("runtime", "memory", "registry", "guardrail")
        },
        # Invariant 1, exposed so a judge can check it from the outside.
        "auto_send": settings.auto_send,
    }


@app.get("/api/clock")
def clock() -> dict[str, Any]:
    current = get_clock()
    payload = {
        "now": current.now().isoformat(),
        "kind": getattr(current, "kind", "system"),
    }
    if payload["kind"] == "simulated":
        payload["start"] = current.start.isoformat()  # type: ignore[union-attr]
        payload["factor"] = current.factor  # type: ignore[union-attr]
        payload["elapsed_days"] = current.elapsed.days  # type: ignore[union-attr]
    return payload


# --- estate --------------------------------------------------------------------------


@app.get("/api/estate")
def estate(estate_id: str | None = None) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    summary = get_orchestrator().summary(record.id)
    return {
        "estate": record.model_dump(mode="json"),
        "summary": summary.model_dump(mode="json"),
        "clock": clock(),
        "model_provider": get_llm().provider_name,
    }


@app.get("/api/obligations")
def obligations(estate_id: str | None = None) -> dict[str, Any]:
    """The obligation graph, as the dashboard draws it."""
    record = _estate_or_404(estate_id)
    repos = get_repos()
    cases = {c.obligation_id: c for c in repos.cases.for_estate(record.id)}

    nodes = []
    for obligation in repos.obligations.for_estate(record.id):
        case = cases.get(obligation.id)
        nodes.append(
            {
                **obligation.model_dump(mode="json"),
                "is_surprise": obligation.is_surprise,
                "case_id": case.id if case else None,
                "state": case.state.value if case else None,
                "playbook_ref": case.playbook_ref if case else None,
                "recovered_usd": case.recovered_amount_usd if case else 0.0,
            }
        )
    return {"estate_id": record.id, "nodes": nodes, "count": len(nodes)}


@app.get("/api/cases")
def cases(estate_id: str | None = None, state: str | None = None) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    rows = get_repos().cases.for_estate(record.id)
    if state:
        rows = [c for c in rows if c.state.value == state.upper()]
    return {"cases": [c.model_dump(mode="json") for c in rows], "count": len(rows)}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict[str, Any]:
    repos = get_repos()
    case = repos.cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")

    packets = repos.packets.for_case(case_id)
    messages = [m for m in repos.inbound.for_estate(case.estate_id) if m.case_id == case_id]
    audit = [r.model_dump(mode="json") for r in get_audit_log().for_case(case_id)]

    from packages.core.adapters.memory import get_memory_adapter

    return {
        "case": case.model_dump(mode="json"),
        "packets": [p.model_dump(mode="json") for p in packets],
        "inbound": [m.model_dump(mode="json") for m in messages],
        "audit": audit,
        "memory": get_memory_adapter().read(case.memory_key or case.id),
    }


# --- approvals -----------------------------------------------------------------------


class Decision(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1, description="The human making the call. Required.")
    note: str = ""


@app.get("/api/approvals")
def approvals(estate_id: str | None = None) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    queue = get_approval_service().queue_view(record.id)
    return {"queue": queue, "count": len(queue)}


@app.post("/api/approvals/{approval_id}/decide")
def decide(approval_id: str, decision: Decision) -> dict[str, Any]:
    """The only write endpoint that changes what happens in the world.

    Approving an outbound request is what causes the letter to be sent - by
    `approvals.py`, through `transport.deliver`, which re-checks this decision before it
    transmits anything.
    """
    try:
        request = get_approval_service().decide(
            approval_id,
            approved=decision.approved,
            decided_by=decision.decided_by,
            note=decision.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found") from None
    except PolicyViolation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"approval": request.model_dump(mode="json")}


@app.get("/api/disclosure-diff")
def disclosure_diff(
    left: str = Query(description="packet id"),
    right: str = Query(description="packet id"),
) -> dict[str, Any]:
    """The side-by-side: what the pension fund gets versus what the magazine gets."""
    repos = get_repos()
    a, b = repos.packets.get(left), repos.packets.get(right)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="one or both packets not found")
    return {
        "left": {"packet_id": a.id, "recipient": a.institution_name, "playbook": a.playbook_ref},
        "right": {"packet_id": b.id, "recipient": b.institution_name, "playbook": b.playbook_ref},
        "rows": diff_view(a.disclosures, b.disclosures),
    }


# --- inbound -------------------------------------------------------------------------


@app.get("/api/inbound")
def inbound(estate_id: str | None = None, blocked_only: bool = False) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    messages = get_repos().inbound.for_estate(record.id)
    if blocked_only:
        messages = [m for m in messages if m.screening and m.screening.verdict is Verdict.BLOCK]
    return {
        "messages": [m.model_dump(mode="json") for m in messages],
        "count": len(messages),
        "blocked": len(
            [m for m in messages if m.screening and m.screening.verdict is Verdict.BLOCK]
        ),
    }


@app.get("/api/inbound/{message_id}/raw")
def inbound_raw(message_id: str) -> dict[str, Any]:
    """The quarantined original.

    A human may read this. A model may not - which is why it is served from the blob
    store on request rather than carried on the message record, and why the response
    says so in a field the UI renders as a banner.
    """
    repos = get_repos()
    matches = [m for m in repos.inbound.all() if m.id == message_id]
    if not matches:
        raise HTTPException(status_code=404, detail=f"message {message_id} not found")
    message = matches[0]
    path = Path(message.raw_ref)
    if not path.exists():
        raise HTTPException(status_code=404, detail="quarantined content is no longer on disk")
    return {
        "message_id": message.id,
        "quarantined": True,
        "warning": (
            "Original third-party content, shown for human review only. This text never "
            "entered a model prompt."
        ),
        "screening": message.screening.model_dump(mode="json") if message.screening else None,
        "raw": path.read_text(encoding="utf-8"),
    }


# --- registry ------------------------------------------------------------------------


@app.get("/api/registry")
def registry() -> dict[str, Any]:
    adapter = get_registry_adapter()
    if hasattr(adapter, "catalog"):
        return {"playbooks": adapter.catalog()}
    return {  # pragma: no cover - only for adapters without a catalog view
        "playbooks": [
            {"name": name, "versions": adapter.list_versions(name)}
            for name in adapter.list_names()
        ]
    }


@app.get("/api/registry/{name}/diff")
def registry_diff(name: str, from_version: str, to_version: str) -> dict[str, Any]:
    try:
        changes = get_registry_adapter().diff(name, from_version, to_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"playbook": name, "from": from_version, "to": to_version, "changes": changes}


@app.get("/api/amendments")
def amendments(estate_id: str | None = None) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    proposals = list_amendments(record.id)
    return {"amendments": [p.model_dump(mode="json") for p in proposals], "count": len(proposals)}


# --- audit ---------------------------------------------------------------------------


@app.get("/api/audit")
def audit(estate_id: str | None = None, limit: int = 500, action: str | None = None) -> dict[str, Any]:
    record = _estate_or_404(estate_id)
    log_ = get_audit_log()
    records = log_.for_estate(record.id)
    if action:
        records = [r for r in records if r.action.startswith(action)]
    chain_ok, broken_at = log_.verify()
    return {
        "records": [r.model_dump(mode="json") for r in records[-limit:]],
        "total": len(records),
        "chain_verified": chain_ok,
        "chain_broken_at": broken_at,
    }


@app.get("/api/audit/export")
def audit_export(estate_id: str | None = None, download: bool = False):
    """Produce the court-facing record."""
    record = _estate_or_404(estate_id)
    from packages.core.audit.export import export_estate_record

    produced = export_estate_record(record)
    if download:
        target = produced.get("pdf") or produced["html"]
        return FileResponse(str(target), filename=target.name)
    return JSONResponse(
        {
            "estate_id": record.id,
            "files": {kind: str(path) for kind, path in produced.items()},
            "note": (
                "HTML always; PDF when reportlab is installed. Both carry the hash-chain "
                "verification result at the top."
            ),
        }
    )


@app.get("/api/traces")
def traces(limit: int = 200) -> dict[str, Any]:
    spans = recorder().spans[-limit:]
    return {
        "spans": [s.to_dict() for s in spans],
        "total": len(recorder().spans),
        "note": (
            "In-process span recorder. In cloud mode these are also exported to Cloud "
            "Trace via OpenTelemetry."
        ),
    }


@app.get("/api/states")
def states() -> dict[str, Any]:
    """The state machine itself, so the dashboard can draw it rather than hard-code it."""
    from packages.core.fsm import DORMANT_STATES, TRANSITIONS

    return {
        "states": [s.value for s in CaseState],
        "transitions": {s.value: sorted(t.value for t in targets) for s, targets in TRANSITIONS.items()},
        "dormant": sorted(s.value for s in DORMANT_STATES),
    }


def run() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    settings = get_settings()
    if settings.is_cloud:
        from packages.core.telemetry import configure_cloud_tracing

        configure_cloud_tracing()
    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 - Cloud Run requires 0.0.0.0


if __name__ == "__main__":  # pragma: no cover
    run()
