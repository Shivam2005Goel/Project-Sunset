"""The simulated executor.

Six weeks of estate administration involve roughly forty approval decisions. Sitting
through them by hand every time you want to test the pipeline is not viable, so the
time-warp runs a scripted executor.

Two rules keep this honest:

1. Every decision it makes is stamped `(simulated executor, demo run)` in the approval
   record and in the audit log. Nobody reading the fiduciary record afterwards can
   mistake a scripted approval for a human one.
2. **It never decides an escalation.** Escalations are exactly the cases where the
   agent determined a human is required, and having a robot answer them would hollow out
   the boundary the whole submission rests on. They stay in the queue - which is why the
   demo ends with two open escalations rather than a tidy zero.
"""

from __future__ import annotations

from packages.core.logging import get_logger
from packages.core.models import ApprovalKind
from packages.core.repos import Repos, get_repos
from services.api.approvals import ApprovalService, get_approval_service

from demo.estate import SIMULATED_EXECUTOR

log = get_logger("demo.executor")

# What a scripted executor is allowed to decide.
AUTO_DECIDED = {ApprovalKind.OUTBOUND, ApprovalKind.PLAYBOOK_AMENDMENT}


class SimulatedExecutor:
    def __init__(
        self,
        approvals: ApprovalService | None = None,
        repos: Repos | None = None,
        name: str = SIMULATED_EXECUTOR,
    ) -> None:
        self._approvals = approvals or get_approval_service()
        self._repos = repos or get_repos()
        self.name = name
        self.decided = 0

    def process(self, estate_id: str) -> int:
        """Decide everything in the queue this executor is permitted to decide."""
        decided = 0
        for request in list(self._approvals.pending(estate_id)):
            if request.kind not in AUTO_DECIDED:
                continue
            note = (
                "Reviewed the draft and the disclosure list; content and recipient are "
                "correct for this institution."
                if request.kind is ApprovalKind.OUTBOUND
                else "Reviewed the playbook diff; the added requirement matches what the "
                "institution asked for."
            )
            self._approvals.decide(
                request.id, approved=True, decided_by=self.name, note=note
            )
            decided += 1
        self.decided += decided
        if decided:
            log.info("executor.decided", estate_id=estate_id, decided=decided)
        return decided

    def open_escalations(self, estate_id: str) -> int:
        return len(
            [
                request
                for request in self._approvals.pending(estate_id)
                if request.kind is ApprovalKind.ESCALATION
            ]
        )
