"""The root orchestrator.

Reads the obligation graph, resolves a playbook per node, opens a case, and spawns one
sub-agent per institution with its own scoped identity. Then it gets out of the way: the
sub-agents own their cases, and the orchestrator's remaining job is scheduling and
summary.

Ordering is not arbitrary. Pensions go first because overpayment recovery accrues from
the date of death; utilities go early because supply decisions are time-sensitive;
subscriptions go last because they only cost money slowly. An orchestrator that fans out
in dictionary order is leaving the executor's money on the table.
"""

from __future__ import annotations

from packages.core.adapters.runtime import AgentSpec, get_runtime_adapter, service_account_for
from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.config import get_settings
from packages.core.fsm import EstateFSM
from packages.core.logging import get_logger
from packages.core.models import (
    CaseState,
    EstateSummary,
    InstitutionCase,
    Obligation,
    ObligationCategory,
)
from packages.core.repos import Repos, get_repos
from packages.core.telemetry import span
from packages.playbooks.publisher import resolve
from services.worker import agent as worker_agent

log = get_logger("orchestrator")

# Lower runs first. The reasons are in the module docstring; keep them there so this
# stays a table and not an essay.
PRIORITY: dict[ObligationCategory, int] = {
    ObligationCategory.PENSION: 0,
    ObligationCategory.LIFE_INSURANCE: 1,
    ObligationCategory.UNCLAIMED_PROPERTY: 1,
    ObligationCategory.UTILITY: 2,
    ObligationCategory.MORTGAGE: 2,
    ObligationCategory.BANK: 3,
    ObligationCategory.BROKERAGE: 3,
    ObligationCategory.CREDIT_CARD: 4,
    ObligationCategory.TELECOM: 5,
    ObligationCategory.GOVERNMENT: 5,
    ObligationCategory.SUBSCRIPTION: 6,
    ObligationCategory.OTHER: 6,
}


class EstateOrchestrator:
    def __init__(self, repos: Repos | None = None, audit: AuditLog | None = None) -> None:
        self._repos = repos or get_repos()
        self._audit = audit or get_audit_log()
        self._fsm = EstateFSM(self._repos.cases, self._audit)
        self._runtime = get_runtime_adapter()
        worker_agent.register(self._runtime)

    # --- planning --------------------------------------------------------------------

    def plan(self, estate_id: str) -> list[InstitutionCase]:
        """One case and one sub-agent per obligation."""
        estate = self._repos.estates.require(estate_id)
        obligations = self._repos.obligations.for_estate(estate_id)

        with span("orchestrator.plan", estate_id=estate_id, obligations=len(obligations)):
            cases: list[InstitutionCase] = []
            for obligation in self._ordered(obligations):
                playbook, ref, specific = resolve(obligation.institution_name, obligation.category)
                existing = self._repos.cases.by_institution(estate_id, obligation.institution_id)
                if existing is not None:
                    # The case survives in the store; the runtime handle does not survive
                    # this process. Re-spawning is what a managed runtime does for you on
                    # cold start, and skipping it here is why a resumed run would find
                    # its dormancy timers firing into an empty fleet.
                    self._spawn(existing.institution_id, estate_id, existing.category.value, ref)
                    cases.append(existing)
                    continue

                case = InstitutionCase(
                    estate_id=estate_id,
                    obligation_id=obligation.id,
                    institution_id=obligation.institution_id,
                    institution_name=obligation.institution_name,
                    category=obligation.category,
                    playbook_ref=ref,
                    state=CaseState.DISCOVERED,
                )
                case.memory_key = f"{estate_id}:{obligation.institution_id}"

                self._fsm.open_case(
                    case,
                    reason=(
                        f"Opened a case for {obligation.institution_name} "
                        f"({obligation.category.value}), discovered by "
                        f"{obligation.discovery_method.value.lower()} with confidence "
                        f"{obligation.confidence:.2f}. Resolved playbook {ref}"
                        + ("." if specific else " - no dedicated playbook, using the generic template.")
                    ),
                )

                self._spawn(obligation.institution_id, estate_id, obligation.category.value, ref)
                cases.append(case)

            self._audit.record(
                estate_id=estate_id,
                actor="orchestrator",
                action="fleet.planned",
                reasoning=(
                    f"Instantiated {len(cases)} institution sub-agents for the estate of "
                    f"{estate.decedent.full_name}, each with its own scoped identity and "
                    f"its own state machine. Ordered by category so that time-sensitive "
                    f"obligations - pensions, insurance, utilities - are notified first."
                ),
                payload={"cases": len(cases)},
            )
            log.info("fleet.planned", estate_id=estate_id, cases=len(cases))
            return cases

    def _spawn(self, institution_id: str, estate_id: str, category: str, playbook_ref: str) -> None:
        """Instantiate one sub-agent.

        Agent Identity: a narrowly scoped service account per sub-agent, so a compromised
        utility agent cannot reach the brokerage credentials.
        """
        settings = get_settings()
        self._runtime.spawn(
            AgentSpec(
                agent_id=institution_id,
                kind="institution",
                estate_id=estate_id,
                institution_id=institution_id,
                handler="institution_agent",
                service_account=service_account_for(institution_id, settings.project_id),
                labels={"category": category, "playbook": playbook_ref},
            )
        )

    def _ordered(self, obligations: list[Obligation]) -> list[Obligation]:
        return sorted(
            obligations,
            key=lambda o: (PRIORITY.get(o.category, 9), -o.confidence, o.institution_name),
        )

    # --- driving ---------------------------------------------------------------------

    def draft_all(self, estate_id: str) -> int:
        """Draft the opening letter for every case still sitting in DISCOVERED."""
        agent = worker_agent.get_agent()
        drafted = 0
        for case in self._ordered_cases(estate_id):
            if case.state is not CaseState.DISCOVERED:
                continue
            agent.draft(case)
            drafted += 1
        log.info("fleet.drafted", estate_id=estate_id, drafted=drafted)
        return drafted

    def _ordered_cases(self, estate_id: str) -> list[InstitutionCase]:
        return sorted(
            self._repos.cases.for_estate(estate_id),
            key=lambda c: (PRIORITY.get(c.category, 9), c.institution_name),
        )

    def tick(self, moment) -> int:
        """Fire any sub-agent whose dormancy timer has expired.

        In cloud mode Cloud Tasks does this; locally the time-warp driver calls it after
        advancing the clock. Same handler either way.
        """
        runtime = self._runtime
        if hasattr(runtime, "run_due"):
            return runtime.run_due(moment)
        return 0  # pragma: no cover - cloud runtime is push-driven

    # --- reporting -------------------------------------------------------------------

    def summary(self, estate_id: str) -> EstateSummary:
        estate = self._repos.estates.require(estate_id)
        cases = self._repos.cases.for_estate(estate_id)
        obligations = self._repos.obligations.for_estate(estate_id)
        pending = self._repos.approvals.pending(estate_id)
        blocked = self._repos.inbound.blocked(estate_id)

        by_state: dict[str, int] = {}
        for case in cases:
            by_state[case.state.value] = by_state.get(case.state.value, 0) + 1

        from packages.core.clock import get_clock

        clock = get_clock()

        return EstateSummary(
            estate_id=estate_id,
            decedent_name=estate.decedent.full_name,
            discovered=len(obligations),
            surprises=len([o for o in obligations if o.is_surprise]),
            closed=by_state.get(CaseState.CLOSED.value, 0),
            escalated=by_state.get(CaseState.ESCALATED.value, 0),
            pending_approval=len(pending),
            in_flight=len([c for c in cases if c.is_open]),
            recovered_usd=round(sum(c.recovered_amount_usd for c in cases), 2),
            injections_blocked=len(blocked),
            simulated_date=clock.now().isoformat() if getattr(clock, "kind", "") == "simulated" else None,
            by_state=by_state,
        )


_orchestrator: EstateOrchestrator | None = None


def get_orchestrator() -> EstateOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = EstateOrchestrator()
    return _orchestrator


def set_orchestrator(orchestrator: EstateOrchestrator | None) -> None:
    global _orchestrator
    _orchestrator = orchestrator
