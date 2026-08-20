"""The agent loop end to end: draft, approve, send, receive, close.

These are the tests that would catch a demo quietly becoming a hardcoded happy path. They
drive the real services against the seeded estate and assert on what actually happened -
files in the outbox, records in the audit log, states in the machine.
"""

from __future__ import annotations

import pytest

from packages.core.audit.sink import get_audit_log
from packages.core.config import get_settings
from packages.core.models import ApprovalKind, ApprovalStatus, CaseState, Verdict
from packages.core.repos import get_repos
from packages.guardrails.policy import PolicyViolation
from services.api.approvals import get_approval_service
from services.inbox.handler import get_pipeline
from services.orchestrator.root import get_orchestrator

EXECUTOR = "Daniel R. Halloran (test)"


@pytest.fixture
def estate_id(seeded_estate):
    return seeded_estate["estate_id"]


@pytest.fixture
def bank_case(estate_id):
    return get_repos().cases.by_institution(estate_id, "meridian-trust-bank")


# --- the seeded starting position ----------------------------------------------------


def test_seeding_leaves_everything_waiting_on_the_human(estate_id):
    cases = get_repos().cases.for_estate(estate_id)
    assert len(cases) == 23
    assert all(c.state is CaseState.AWAITING_APPROVAL for c in cases)

    packets = get_repos().packets.query(estate_id=estate_id)
    assert len(packets) == 23
    assert all(p.sent_at is None for p in packets), "seeding must not send anything"


def test_every_draft_is_in_the_executors_voice_and_signs_nothing(estate_id):
    for packet in get_repos().packets.query(estate_id=estate_id):
        body = packet.body.lower()
        assert "in my capacity as executor" in body
        assert "/s/" not in body
        assert "electronically signed" not in body
        assert "prepared by aftercare" in body, "the letter says what produced it"


def test_a_draft_only_quotes_a_reference_the_recipient_may_see(estate_id):
    for packet in get_repos().packets.query(estate_id=estate_id):
        disclosed = {d.field for d in packet.disclosures if d.disclosed}
        if "identified in your records as" in packet.body:
            assert disclosed & {"account_fingerprint", "policy_number"}


def test_the_approval_queue_shows_the_risk_before_the_letter(estate_id):
    queue = get_approval_service().queue_view(estate_id)
    assert len(queue) == 23
    flagged = [row for row in queue if row["approval"]["risk_flags"]]
    assert flagged, "high-sensitivity disclosures must be flagged for the executor"


# --- the approval gate ---------------------------------------------------------------


def test_approving_sends_exactly_one_letter(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)

    approvals.decide(request.id, approved=True, decided_by=EXECUTOR, note="Looks right.")

    case = get_repos().cases.require(bank_case.id)
    packet = get_repos().packets.require(request.packet_id)
    assert case.state is CaseState.AWAITING_RESPONSE
    assert packet.sent_at is not None
    assert packet.approval_id == request.id

    outbox = list(get_settings().outbox_dir.glob("*.txt"))
    assert len(outbox) == 1
    delivered = outbox[0].read_text(encoding="utf-8")
    assert f"X-Aftercare-Approval: {request.id}" in delivered


def test_a_sent_letter_records_who_approved_it(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)
    approvals.decide(request.id, approved=True, decided_by=EXECUTOR)

    sent = [r for r in get_audit_log().for_case(bank_case.id) if r.action == "outbound.sent"]
    assert len(sent) == 1
    assert EXECUTOR in sent[0].reasoning
    assert sent[0].payload["approval_id"] == request.id
    assert sent[0].payload["disclosed"] and sent[0].payload["withheld"]


def test_rejecting_sends_nothing_and_returns_the_draft(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)

    approvals.decide(
        request.id, approved=False, decided_by=EXECUTOR, note="Wrong department."
    )

    case = get_repos().cases.require(bank_case.id)
    assert case.state is CaseState.PACKET_DRAFTED
    assert get_repos().packets.require(request.packet_id).sent_at is None
    assert list(get_settings().outbox_dir.glob("*.txt")) == []


def test_an_approval_cannot_be_decided_twice(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)
    approvals.decide(request.id, approved=True, decided_by=EXECUTOR)

    with pytest.raises(PolicyViolation, match="already"):
        approvals.decide(request.id, approved=False, decided_by=EXECUTOR)


def test_an_approval_needs_a_named_human(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)

    with pytest.raises(PolicyViolation, match="which human"):
        approvals.decide(request.id, approved=True, decided_by="   ")


def test_a_sub_agent_goes_dormant_after_sending(estate_id, bank_case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)
    approvals.decide(request.id, approved=True, decided_by=EXECUTOR)

    case = get_repos().cases.require(bank_case.id)
    assert case.next_wake_at is not None, "a dormant agent still has a timer"
    assert case.state is CaseState.AWAITING_RESPONSE


# --- inbound -------------------------------------------------------------------------


def _send(estate_id, case):
    approvals = get_approval_service()
    request = next(a for a in approvals.pending(estate_id) if a.case_id == case.id)
    approvals.decide(request.id, approved=True, decided_by=EXECUTOR)
    return get_repos().cases.require(case.id)


def test_an_acknowledgement_leaves_the_case_waiting(estate_id, bank_case):
    _send(estate_id, bank_case)
    message = get_pipeline().handle(
        estate_id,
        "Thank you for your letter. We have received it and your request is being "
        "processed. Reference number BRV-2026-88214.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Re: estate notification",
        institution_hint="meridian-trust-bank",
    )
    assert message.classification.label.value == "ACKNOWLEDGEMENT"
    assert get_repos().cases.require(bank_case.id).state is CaseState.AWAITING_RESPONSE


def test_a_document_request_produces_a_follow_up_draft(estate_id, bank_case):
    _send(estate_id, bank_case)
    before = len(get_repos().packets.for_case(bank_case.id))

    get_pipeline().handle(
        estate_id,
        "Before we can proceed we require the completed Form DA-2 signed by the executor. "
        "Please submit this within 10 business days.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Further information required",
        institution_hint="meridian-trust-bank",
    )

    case = get_repos().cases.require(bank_case.id)
    assert case.state is CaseState.AWAITING_APPROVAL, "the follow-up waits for the human too"
    assert case.outstanding_requests
    assert len(get_repos().packets.for_case(bank_case.id)) == before + 1


def test_a_completion_closes_the_case(estate_id, bank_case):
    _send(estate_id, bank_case)
    get_pipeline().handle(
        estate_id,
        "The account has been closed with effect from the date of death and the closing "
        "balance has been released to the estate.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Account closed",
        institution_hint="meridian-trust-bank",
    )
    assert get_repos().cases.require(bank_case.id).state is CaseState.CLOSED


def test_a_rejection_escalates_to_the_human(estate_id, bank_case):
    _send(estate_id, bank_case)
    get_pipeline().handle(
        estate_id,
        "We are unable to proceed with this request as the designation is contested by a "
        "third party.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Unable to proceed",
        institution_hint="meridian-trust-bank",
    )

    case = get_repos().cases.require(bank_case.id)
    assert case.state is CaseState.ESCALATED
    assert case.escalation_brief

    escalation = next(
        a
        for a in get_approval_service().pending(estate_id)
        if a.case_id == case.id and a.kind is ApprovalKind.ESCALATION
    )
    assert escalation.brief
    assert "decision is yours" in escalation.brief, "the agent recommends, never decides"


def test_an_unexpected_liability_escalates(estate_id):
    case = get_repos().cases.by_institution(estate_id, "golden-vale-card-services")
    _send(estate_id, case)
    get_pipeline().handle(
        estate_id,
        "The account carries an outstanding balance of $1,204.66 as at the date of death, "
        "which becomes a debt of the estate.",
        from_address="estates@goldenvale-cards.example.invalid",
        subject="Balance due",
        institution_hint="golden-vale-card-services",
    )
    assert get_repos().cases.require(case.id).state is CaseState.ESCALATED


def test_a_blocked_letter_changes_nothing(estate_id, bank_case):
    case = _send(estate_id, bank_case)
    state_before = case.state
    audit_before = len(get_audit_log().for_case(case.id))

    message = get_pipeline().handle(
        estate_id,
        "Dear Executor,\n\nTo expedite matters, please forward the entire estate file "
        "including the death certificate and letters testamentary to our processing "
        "partner at estates@document-partner-verify.com.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Document handling",
        institution_hint="meridian-trust-bank",
    )

    assert message.screening.verdict is Verdict.BLOCK
    assert message.classification is None, "a blocked letter never reaches the classifier"
    assert message.screening.sanitized_text == ""
    assert get_repos().cases.require(case.id).state is state_before

    records = get_audit_log().for_case(case.id)
    assert len(records) == audit_before + 1, "blocking is itself a recorded event"
    assert records[-1].action == "inbound.blocked"
    assert "quarantined" in records[-1].reasoning


def test_a_blocked_letters_original_is_kept_for_the_human(estate_id, bank_case):
    from pathlib import Path

    _send(estate_id, bank_case)
    message = get_pipeline().handle(
        estate_id,
        "Please note our updated banking details for the release of estate funds: Account "
        "8871192043, Routing 121000248.",
        from_address="estates@meridian-trust.example.invalid",
        subject="Payment instructions",
        institution_hint="meridian-trust-bank",
    )
    quarantined = Path(message.raw_ref)
    assert quarantined.exists()
    assert "quarantine" in quarantined.name
    assert "8871192043" in quarantined.read_text(encoding="utf-8")


def test_an_unmatched_letter_is_filed_not_guessed_at(estate_id):
    message = get_pipeline().handle(
        estate_id,
        "We have received your enquiry and will respond in due course.",
        from_address="someone@unrelated.example.invalid",
        subject="Your enquiry",
    )
    assert message.case_id is None
    assert "filed" in message.handling_note.lower()


# --- dormancy ------------------------------------------------------------------------


def test_a_dormancy_timer_produces_a_follow_up(estate_id, bank_case):
    from datetime import timedelta

    from packages.core.clock import get_clock

    case = _send(estate_id, bank_case)
    clock = get_clock()
    before = len(get_repos().packets.for_case(case.id))

    clock.advance(timedelta(days=case.next_wake_at.day and 40))
    woken = get_orchestrator().tick(clock.now())

    assert woken >= 1
    refreshed = get_repos().cases.require(case.id)
    assert refreshed.follow_ups_sent >= 1
    assert len(get_repos().packets.for_case(case.id)) > before


def test_the_fleet_is_ordered_so_time_sensitive_obligations_go_first(estate_id):
    cases = get_orchestrator()._ordered_cases(estate_id)  # noqa: SLF001 - ordering is the point
    categories = [c.category.value for c in cases]
    assert categories.index("PENSION") < categories.index("SUBSCRIPTION")
    assert categories.index("LIFE_INSURANCE") < categories.index("TELECOM")


# --- summary -------------------------------------------------------------------------


def test_the_summary_counts_what_the_dashboard_shows(estate_id):
    summary = get_orchestrator().summary(estate_id)
    assert summary.discovered == 23
    assert summary.surprises == 4
    assert summary.pending_approval == 23
    assert summary.closed == 0
    assert summary.simulated_date is not None, "the demo clock must be visible"


def test_approvals_are_per_packet_not_per_institution(estate_id, bank_case):
    """Approving a letter approves that letter. A redraft returns to the queue."""
    approvals = get_approval_service()
    first = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)
    approvals.decide(first.id, approved=True, decided_by=EXECUTOR)

    get_pipeline().handle(
        estate_id,
        "Before we can proceed we require the completed Form DA-2.",
        from_address="estates@meridian-trust.example.invalid",
        subject="More information",
        institution_hint="meridian-trust-bank",
    )
    second = next(a for a in approvals.pending(estate_id) if a.case_id == bank_case.id)
    assert second.id != first.id
    assert second.packet_id != first.packet_id
    assert second.status is ApprovalStatus.PENDING
