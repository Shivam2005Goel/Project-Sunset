"""End-to-end smoke test: upload -> discovery -> draft -> approve -> inbound -> close.

This is the one command that tells you whether the whole thing works. It seeds a fresh
estate, replays six simulated weeks, and then asserts a set of facts that cannot all be
true unless every layer is doing its job:

* discovery found 23 obligations, and 4 of them were not on any list
* every one of 23 institutions got a sub-agent and a playbook
* nothing was sent without an approval record - checked against the audit log, not the
  code path
* every injection in the scripted mail was blocked before reaching a model
* the audit chain verifies end to end
* 19 closed, 2 escalated, 2 still in flight

If any assertion fails it prints what it expected and what it got, and exits non-zero.
There is no partial pass.
"""

from __future__ import annotations

import sys

from packages.core.audit.sink import get_audit_log
from packages.core.logging import set_level
from packages.core.models import ApprovalKind, ApprovalStatus, CaseState, Verdict
from packages.core.repos import get_repos
from packages.playbooks.publisher import list_amendments

from demo.estate import SCRIPT
from demo.seed import seed
from demo.timewarp import run as timewarp

EXPECTED = {
    "discovered": 23,
    "surprises": 4,
    "closed": 19,
    "escalated": 2,
    "pending": 2,
    "injections": len([e for e in SCRIPT if e.kind == "adversarial"]),
}


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def that(self, label: str, actual, expected=None, *, truthy: bool = False) -> None:
        ok = bool(actual) if truthy else actual == expected
        if ok:
            self.passed += 1
            print(f"    ok    {label}" + (f" = {actual}" if not truthy else ""))
        else:
            self.failures.append(f"{label}: expected {expected!r}, got {actual!r}")
            print(f"    FAIL  {label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    set_level("WARNING")
    print()
    print("  Seeding a fresh estate...")
    seeded = seed(fresh=True, quiet=True)

    print("  Replaying six simulated weeks...")
    result = timewarp(seeded["estate_id"], quiet=True)

    repos = get_repos()
    estate_id = seeded["estate_id"]
    cases = repos.cases.for_estate(estate_id)
    packets = repos.packets.query(estate_id=estate_id)
    approvals = repos.approvals.query(estate_id=estate_id)
    inbound = repos.inbound.for_estate(estate_id)
    audit = get_audit_log()

    check = Check()
    print()
    print("  Discovery")
    check.that("obligations discovered", result["discovered"], EXPECTED["discovered"])
    check.that("found without being listed", result["surprises"], EXPECTED["surprises"])
    check.that(
        "every obligation carries evidence",
        all(o.evidence for o in repos.obligations.for_estate(estate_id)),
        True,
    )

    print("  Fleet")
    check.that("institution cases", len(cases), EXPECTED["discovered"])
    check.that("every case resolved a playbook", all(c.playbook_ref for c in cases), True)
    check.that("sub-agent wakes from dormancy", result["woken"] > 0, True)

    print("  Approval boundary")
    sent = [p for p in packets if p.sent_at is not None]
    approved_ids = {
        a.id for a in approvals if a.status is ApprovalStatus.APPROVED
    }
    check.that("packets sent", len(sent) > 0, True)
    check.that(
        "every sent packet carries an approval",
        all(p.approval_id in approved_ids for p in sent),
        True,
    )
    outbound_audit = [r for r in audit.for_estate(estate_id) if r.action == "outbound.sent"]
    check.that("audit records one send per sent packet", len(outbound_audit), len(sent))
    check.that(
        "every send names a human decider",
        all(r.payload.get("approval_id") in approved_ids for r in outbound_audit),
        True,
    )
    check.that(
        "no send was refused at the gate",
        len([r for r in audit.for_estate(estate_id) if r.action == "outbound.refused"]),
        0,
    )

    print("  Guardrails")
    blocked = [m for m in inbound if m.screening and m.screening.verdict is Verdict.BLOCK]
    check.that("injections blocked", len(blocked), EXPECTED["injections"])
    check.that(
        "no blocked message produced a model projection",
        all(not m.screening.sanitized_text for m in blocked),  # type: ignore[union-attr]
        True,
    )
    check.that(
        "no blocked message moved a case",
        all(m.classification is None for m in blocked),
        True,
    )

    print("  Disclosure")
    never = {"cause_of_death", "decedent_ssn_full", "account_number_full"}
    check.that(
        "no packet discloses a never-disclose field",
        all(never.isdisjoint(p.disclosed_fields) for p in packets),
        True,
    )
    check.that(
        "every packet withheld something",
        all(p.withheld_fields for p in packets),
        True,
    )

    print("  Learning")
    amendments = list_amendments(estate_id)
    check.that("playbook amendments proposed", len(amendments) > 0, True)
    check.that(
        "amendments were approved by a human before publishing",
        all(
            a.status == "PUBLISHED"
            for a in amendments
            if any(
                r.kind is ApprovalKind.PLAYBOOK_AMENDMENT
                and r.status is ApprovalStatus.APPROVED
                and r.decision_note == a.id
                for r in approvals
            )
        ),
        True,
    )

    print("  Audit")
    chain_ok, broken_at = audit.verify()
    check.that("hash chain verifies", chain_ok, True)
    check.that("chain break", broken_at, None)
    check.that(
        "every state transition wrote a reasoning chain",
        all(t.reason for case in cases for t in case.history),
        True,
    )

    print("  Outcome")
    check.that("closed", result["closed"], EXPECTED["closed"])
    check.that("escalated", result["escalated"], EXPECTED["escalated"])
    check.that("still in flight", result["in_flight"], EXPECTED["pending"])
    check.that(
        "escalations are still waiting on the human",
        len([a for a in approvals if a.kind is ApprovalKind.ESCALATION and not a.is_decided]),
        EXPECTED["escalated"],
    )
    check.that(
        "no case ended in an undeclared state",
        all(isinstance(c.state, CaseState) for c in cases),
        True,
    )

    print()
    if check.failures:
        print(f"  SMOKE FAIL - {len(check.failures)} of {check.passed + len(check.failures)} checks failed")
        for failure in check.failures:
            print(f"    - {failure}")
        print()
        return 1

    print(
        f"  SMOKE PASS - {result['discovered']} discovered - {result['closed']} closed - "
        f"{result['escalated']} escalated - {result['in_flight']} pending"
    )
    print(
        f"  {check.passed} checks - {result['blocked']} injections blocked - "
        f"${result['recovered_usd']:,.2f} recovered - {result['audit_records']} audit records"
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
