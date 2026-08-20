"""The approval queue. Invariant 1 lives here.

Every outbound communication, every escalation, and every playbook amendment passes
through this service. It is the only module allowed to import `transport.deliver`, and
`tests/test_policy.py` enforces that by walking the AST of the whole repository.

The design decision worth defending: **approval is per-packet, not per-institution and
not per-session.** An executor who approves a letter to a bank has approved *that letter*.
If the draft changes, the approval is void and the packet returns to the queue - which is
why `ApprovalRequest.packet_id` is checked at the gate rather than `case_id`.
"""

from __future__ import annotations

from typing import Any

from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.clock import now
from packages.core.fsm import EstateFSM
from packages.core.logging import get_logger
from packages.core.models import (
    ApprovalKind,
    ApprovalRequest,
    ApprovalStatus,
    CaseState,
    ClosurePacket,
    InstitutionCase,
)
from packages.core.repos import Repos, get_repos
from packages.core.telemetry import span
from packages.guardrails.policy import PolicyViolation, assert_draft_clean, risk_flags
from services.api.transport import deliver

log = get_logger("approvals")


class ApprovalService:
    def __init__(
        self,
        repos: Repos | None = None,
        fsm: EstateFSM | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._repos = repos or get_repos()
        self._audit = audit or get_audit_log()
        self._fsm = fsm or EstateFSM(self._repos.cases, self._audit)

    # --- requesting ------------------------------------------------------------------

    def request_outbound(
        self,
        case: InstitutionCase,
        packet: ClosurePacket,
        *,
        disclosure_summary: dict[str, Any],
        summary: str = "",
    ) -> ApprovalRequest:
        """Queue a drafted packet for the executor.

        The boundary scan runs here rather than at send time, so a draft that violates a
        boundary never reaches the executor's queue at all - they should not have to be
        the check for something the code can check.
        """
        assert_draft_clean(packet)

        request = ApprovalRequest(
            estate_id=case.estate_id,
            case_id=case.id,
            kind=ApprovalKind.OUTBOUND,
            packet_id=packet.id,
            summary=summary or f"Closure request to {case.institution_name}",
            risk_flags=risk_flags(packet, disclosure_summary),
        )
        self._repos.approvals.save(request)

        case.approval_id = request.id
        case.packet_id = packet.id
        self._repos.cases.save(case)

        if case.state is not CaseState.AWAITING_APPROVAL:
            self._fsm.transition(
                case,
                CaseState.AWAITING_APPROVAL,
                event="approval.requested",
                reason=(
                    f"Draft to {case.institution_name} is complete and is waiting on the "
                    f"executor. Disclosing {disclosure_summary.get('disclosed_count', 0)} "
                    f"field(s), withholding {disclosure_summary.get('withheld_count', 0)}. "
                    f"Nothing leaves the system until this is approved."
                ),
                actor="worker",
            )

        log.bind(estate_id=case.estate_id, institution_id=case.institution_id).info(
            "approval.requested", approval_id=request.id, packet_id=packet.id
        )
        return request

    def request_escalation(
        self,
        case: InstitutionCase,
        *,
        brief: str,
        trigger: str,
        options: list[str] | None = None,
    ) -> ApprovalRequest:
        """Boundary 4: ambiguity escalates within one turn, with a one-paragraph brief."""
        request = ApprovalRequest(
            estate_id=case.estate_id,
            case_id=case.id,
            kind=ApprovalKind.ESCALATION,
            summary=f"Decision needed: {case.institution_name}",
            brief=brief,
            risk_flags=[trigger, *(options or [])],
        )
        self._repos.approvals.save(request)

        case.escalation_brief = brief
        case.approval_id = request.id
        self._repos.cases.save(case)

        if case.state is not CaseState.ESCALATED:
            self._fsm.transition(
                case,
                CaseState.ESCALATED,
                event="escalation.raised",
                reason=f"Escalated to the executor: {trigger}. {brief[:400]}",
                actor="worker",
            )
        log.bind(estate_id=case.estate_id, institution_id=case.institution_id).info(
            "escalation.raised", approval_id=request.id
        )
        return request

    def request_amendment(self, case: InstitutionCase, proposal: Any) -> ApprovalRequest:
        """A playbook version bump is a change to a shared asset - the executor sees the
        diff before every future estate inherits it."""
        request = ApprovalRequest(
            estate_id=case.estate_id,
            case_id=case.id,
            kind=ApprovalKind.PLAYBOOK_AMENDMENT,
            summary=(
                f"Publish {proposal.playbook_name} v{proposal.proposed_version} "
                f"(from v{proposal.from_version})"
            ),
            brief=proposal.rationale,
            risk_flags=[f"adds: {doc}" for doc in proposal.add_required_documents],
        )
        request.decision_note = proposal.id  # carries the proposal id through the decision
        self._repos.approvals.save(request)

        case.amendment_proposed = proposal.id
        self._repos.cases.save(case)
        return request

    # --- deciding --------------------------------------------------------------------

    def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        note: str = "",
    ) -> ApprovalRequest:
        """Record the executor's decision, then act on it.

        `decided_by` is required and is written into the audit record. An approval
        without a named human is not an approval, and the gate in `transport.deliver`
        rejects one.
        """
        request = self._repos.approvals.require(approval_id)
        if request.is_decided:
            raise PolicyViolation(
                f"approval {approval_id} was already {request.status.value} at "
                f"{request.decided_at}. Decisions are not revisited; raise a new one."
            )
        if not decided_by.strip():
            raise PolicyViolation("an approval must record which human made the decision")

        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.decided_at = now()
        request.decided_by = decided_by
        if request.kind is not ApprovalKind.PLAYBOOK_AMENDMENT:
            request.decision_note = note
        self._repos.approvals.save(request)

        self._audit.record(
            estate_id=request.estate_id,
            case_id=request.case_id,
            actor=decided_by,
            action=f"approval.{request.status.value.lower()}",
            reasoning=(
                f"Executor {decided_by} {request.status.value.lower()} "
                f"{request.kind.value.lower()} request {request.id}."
                + (f" Note: {note}" if note else "")
            ),
            payload={"approval_id": request.id, "kind": request.kind.value},
        )

        case = self._repos.cases.require(request.case_id)
        if request.kind is ApprovalKind.OUTBOUND:
            self._on_outbound_decision(request, case, approved)
        elif request.kind is ApprovalKind.ESCALATION:
            self._on_escalation_decision(request, case, approved, note)
        elif request.kind is ApprovalKind.PLAYBOOK_AMENDMENT:
            self._on_amendment_decision(request, approved)

        return request

    def _on_outbound_decision(
        self, request: ApprovalRequest, case: InstitutionCase, approved: bool
    ) -> None:
        packet = self._repos.packets.require(request.packet_id or "")
        if not approved:
            self._fsm.transition(
                case,
                CaseState.PACKET_DRAFTED,
                event="approval.rejected",
                reason=(
                    f"Executor rejected the draft to {case.institution_name}"
                    + (f": {request.decision_note}" if request.decision_note else ".")
                    + " The packet returns to drafting; nothing was sent."
                ),
                actor=request.decided_by or "executor",
            )
            return

        packet.approval_id = request.id
        with span("approvals.send", institution=case.institution_name):
            # The only call to deliver() in the codebase.
            deliver(packet, request, case)
        self._repos.packets.save(packet)

        self._fsm.transition(
            case,
            CaseState.SENT,
            event="outbound.sent",
            reason=(
                f"Sent to {packet.recipient} after approval {request.id} by "
                f"{request.decided_by}."
            ),
            actor="transport",
        )
        self._go_dormant(case, packet)

    def _go_dormant(self, case: InstitutionCase, packet: ClosurePacket) -> None:
        """Hand the case to the runtime and stop holding it.

        The sub-agent now waits - for a reply, or for its own follow-up timer. It holds
        no CPU in between, which is the property the whole architecture is built around.
        """
        from packages.core.adapters.runtime import get_runtime_adapter
        from packages.playbooks.publisher import resolve

        playbook, _, _ = resolve(case.institution_name, case.category)
        self._fsm.transition(
            case,
            CaseState.AWAITING_RESPONSE,
            event="agent.dormant",
            reason=(
                f"Waiting on {case.institution_name}. Their published turnaround is "
                f"{playbook.typical_sla_days} days; the sub-agent sleeps and wakes on "
                f"inbound mail, or on {playbook.follow_up_after_days} days without one."
            ),
            actor="worker",
            wake_in_days=playbook.follow_up_after_days,
        )

        runtime = get_runtime_adapter()
        if case.next_wake_at is not None:
            runtime.schedule(
                case.institution_id,
                case.next_wake_at,
                {"type": "follow_up_timer", "case_id": case.id},
            )

    def _on_escalation_decision(
        self, request: ApprovalRequest, case: InstitutionCase, approved: bool, note: str
    ) -> None:
        """An escalation decision is an instruction, not a send.

        Approving means "proceed as recommended"; the case goes back to drafting so the
        next letter reflects the decision. Rejecting closes the matter out of the
        automated flow entirely - the executor is handling it themselves.
        """
        if approved:
            self._fsm.transition(
                case,
                CaseState.PACKET_DRAFTED,
                event="escalation.resolved",
                reason=(
                    f"Executor {request.decided_by} directed how to proceed with "
                    f"{case.institution_name}"
                    + (f": {note}" if note else ".")
                ),
                actor=request.decided_by or "executor",
            )
        else:
            self._fsm.transition(
                case,
                CaseState.CLOSED,
                event="escalation.taken_over",
                reason=(
                    f"Executor {request.decided_by} is handling {case.institution_name} "
                    f"outside the automated flow"
                    + (f": {note}" if note else ".")
                ),
                actor=request.decided_by or "executor",
            )

    def _on_amendment_decision(self, request: ApprovalRequest, approved: bool) -> None:
        from packages.playbooks.publisher import apply_amendment

        proposal_id = request.decision_note or ""
        if approved and proposal_id:
            ref = apply_amendment(proposal_id)
            self._audit.record(
                estate_id=request.estate_id,
                case_id=request.case_id,
                actor=request.decided_by or "executor",
                action="playbook.published",
                reasoning=(
                    f"Playbook amendment {ref} published to the registry. Every future "
                    f"estate touching this institution now starts from the better version."
                ),
                payload={"ref": ref, "proposal_id": proposal_id},
            )

    # --- reading ---------------------------------------------------------------------

    def pending(self, estate_id: str) -> list[ApprovalRequest]:
        return self._repos.approvals.pending(estate_id)

    def queue_view(self, estate_id: str) -> list[dict[str, Any]]:
        """What the dashboard's approval queue renders."""
        rows = []
        for request in self.pending(estate_id):
            case = self._repos.cases.get(request.case_id)
            packet = self._repos.packets.get(request.packet_id) if request.packet_id else None
            rows.append(
                {
                    "approval": request.model_dump(mode="json"),
                    "institution": case.institution_name if case else "?",
                    "category": case.category.value if case else "OTHER",
                    "state": case.state.value if case else "?",
                    "packet": packet.model_dump(mode="json") if packet else None,
                }
            )
        return rows


_service: ApprovalService | None = None


def get_approval_service() -> ApprovalService:
    global _service
    if _service is None:
        _service = ApprovalService()
    return _service


def set_approval_service(service: ApprovalService | None) -> None:
    global _service
    _service = service
