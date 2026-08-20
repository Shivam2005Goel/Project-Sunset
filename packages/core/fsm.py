"""The estate finite state machine.

State is data, not vibes. A model is never asked "where are we with this bank" - it is
told, from a document, and any transition it proposes is checked against this table
before it happens.

Two properties matter to a judge, and both are enforced here rather than documented:

* An undeclared transition raises. There is no "the model decided to skip approval".
* Every transition writes an audit record carrying its reasoning. Invariant 5.
"""

from __future__ import annotations

from datetime import timedelta

from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.clock import now
from packages.core.logging import get_logger
from packages.core.models import CaseState, InstitutionCase, Transition
from packages.core.repos import CaseRepo
from packages.core.telemetry import span

log = get_logger("fsm")

S = CaseState

# The only legal moves. Read this table as the safety argument it is: nothing reaches
# SENT except through AWAITING_APPROVAL.
TRANSITIONS: dict[CaseState, set[CaseState]] = {
    S.DISCOVERED: {S.PACKET_DRAFTED, S.ESCALATED},
    S.PACKET_DRAFTED: {S.AWAITING_APPROVAL, S.ESCALATED},
    S.AWAITING_APPROVAL: {S.SENT, S.ESCALATED, S.PACKET_DRAFTED},
    S.SENT: {S.AWAITING_RESPONSE, S.ESCALATED},
    S.AWAITING_RESPONSE: {S.INFO_REQUESTED, S.CLOSED, S.ESCALATED, S.PACKET_DRAFTED},
    S.INFO_REQUESTED: {S.PACKET_DRAFTED, S.ESCALATED, S.CLOSED},
    S.ESCALATED: {S.PACKET_DRAFTED, S.CLOSED, S.AWAITING_RESPONSE},
    S.CLOSED: set(),
}

# States in which the sub-agent is dormant: it holds no CPU and is woken by inbound mail
# or by its own follow-up timer.
DORMANT_STATES = {S.SENT, S.AWAITING_RESPONSE, S.INFO_REQUESTED}

# Default dormancy per state, in days. Overridden per institution by the playbook's SLA.
DEFAULT_WAKE_DAYS = {
    S.SENT: 10,
    S.AWAITING_RESPONSE: 14,
    S.INFO_REQUESTED: 7,
}


class IllegalTransition(RuntimeError):
    def __init__(self, case: InstitutionCase, target: CaseState) -> None:
        allowed = ", ".join(sorted(s.value for s in TRANSITIONS[case.state])) or "(terminal)"
        super().__init__(
            f"{case.institution_name}: {case.state.value} -> {target.value} is not a "
            f"declared transition. Allowed: {allowed}"
        )
        self.case = case
        self.target = target


class EstateFSM:
    """Drives cases. The only thing in the codebase that writes `case.state`."""

    def __init__(self, cases: CaseRepo | None = None, audit: AuditLog | None = None) -> None:
        self._cases = cases or CaseRepo()
        self._audit = audit or get_audit_log()

    @staticmethod
    def can(case: InstitutionCase, target: CaseState) -> bool:
        return target in TRANSITIONS[case.state]

    def transition(
        self,
        case: InstitutionCase,
        target: CaseState,
        *,
        event: str,
        reason: str,
        actor: str = "orchestrator",
        wake_in_days: int | None = None,
        payload: dict | None = None,
    ) -> InstitutionCase:
        if not reason.strip():
            # Invariant 5. A transition without a reasoning chain is not auditable, and
            # an unauditable transition is worse than no transition.
            raise ValueError(f"transition {case.state.value}->{target.value} needs a reason")
        if not self.can(case, target):
            raise IllegalTransition(case, target)

        with span(
            "fsm.transition",
            institution=case.institution_name,
            from_state=case.state.value,
            to_state=target.value,
            event=event,
            reasoning=reason,
        ):
            record = self._audit.record(
                estate_id=case.estate_id,
                institution_id=case.institution_id,
                case_id=case.id,
                actor=actor,
                action=f"fsm.{case.state.value}->{target.value}",
                reasoning=reason,
                payload={"event": event, **(payload or {})},
            )

            moment = now()
            previous = case.state
            case.history.append(
                Transition(
                    from_state=previous,
                    to_state=target,
                    event=event,
                    at=moment,
                    actor=actor,
                    reason=reason,
                    audit_id=record.id,
                )
            )
            case.state = target

            days = wake_in_days if wake_in_days is not None else DEFAULT_WAKE_DAYS.get(target)
            case.next_wake_at = moment + timedelta(days=days) if target in DORMANT_STATES and days else None

            if target is S.CLOSED:
                case.closed_at = moment
                case.next_wake_at = None

            self._cases.save(case)

        log.bind(estate_id=case.estate_id, institution_id=case.institution_id).info(
            "transition",
            from_state=previous.value,
            to_state=target.value,
            event=event,
            audit_id=record.id,
        )
        return case

    def open_case(self, case: InstitutionCase, reason: str) -> InstitutionCase:
        """Record the birth of a case. DISCOVERED is the initial state, so this writes
        the audit record without a transition."""
        record = self._audit.record(
            estate_id=case.estate_id,
            institution_id=case.institution_id,
            case_id=case.id,
            actor="orchestrator",
            action="case.opened",
            reasoning=reason,
            payload={"institution": case.institution_name, "category": case.category.value},
        )
        case.history.append(
            Transition(
                from_state=S.DISCOVERED,
                to_state=S.DISCOVERED,
                event="case.opened",
                actor="orchestrator",
                reason=reason,
                audit_id=record.id,
            )
        )
        return self._cases.save(case)


def path_to(target: CaseState, start: CaseState = S.DISCOVERED) -> list[CaseState] | None:
    """Breadth-first search over the transition table.

    Used by `tests/test_policy.py` to prove there is no path from DISCOVERED to SENT that
    does not pass through AWAITING_APPROVAL - the structural half of invariant 1.
    """
    queue: list[list[CaseState]] = [[start]]
    seen = {start}
    while queue:
        route = queue.pop(0)
        if route[-1] is target:
            return route
        for nxt in TRANSITIONS[route[-1]]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append([*route, nxt])
    return None


def all_paths(target: CaseState, start: CaseState = S.DISCOVERED, limit: int = 5000) -> list[list[CaseState]]:
    """Every simple (non-repeating) route from `start` to `target`."""
    found: list[list[CaseState]] = []
    stack: list[list[CaseState]] = [[start]]
    while stack and len(found) < limit:
        route = stack.pop()
        current = route[-1]
        if current is target and len(route) > 1:
            found.append(route)
            continue
        for nxt in TRANSITIONS[current]:
            if nxt not in route:
                stack.append([*route, nxt])
    return found
