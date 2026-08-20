"""Runtime policy enforcement for the safety boundaries.

Boundary 3 (nothing sends without approval) is structural - the send path physically
cannot be reached without an approval record, and `assert_sendable` below is the assertion
at that gate. Boundaries 1, 2 and 4 are textual properties of a draft, checked here.

`tests/test_policy.py` proves the structural half by static analysis of the source tree.
This module proves the behavioural half at runtime. Both run in CI; neither is optional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.core.models import (
    ApprovalRequest,
    ApprovalStatus,
    CaseState,
    ClosurePacket,
    CorrespondenceClass,
    InstitutionCase,
)


class PolicyViolation(RuntimeError):
    """Raised at the gate. Never caught to "keep the demo moving"."""


@dataclass(frozen=True)
class PolicyRule:
    name: str
    pattern: re.Pattern[str]
    message: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Boundary 1: the agent never signs anything and never asserts authority it lacks.
SIGNATURE_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        "signature.electronic",
        _rx(r"\b(/s/|electronically signed|e-?signed|digitally signed|signature on file|signed:\s*\S)"),
        "Draft contains a signature assertion. Aftercare never signs.",
    ),
    PolicyRule(
        "authority.power_of_attorney",
        _rx(r"\b(power of attorney|attorney-in-fact|duly authorised agent|duly authorized agent|acting with full authority)\b"),
        "Draft claims a delegated legal authority the agent does not hold.",
    ),
    PolicyRule(
        "authority.binding_commitment",
        _rx(r"\b(I hereby (certify|declare|affirm|swear|attest)|under penalty of perjury|this constitutes (a )?legal notice)\b"),
        "Draft makes a sworn or binding assertion.",
    ),
)

# Boundary 2: the agent never makes a legal determination.
LEGAL_DETERMINATION_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        "legal.entitlement",
        _rx(r"\b(is|are) (the )?(rightful|lawful|sole|legal) (heir|beneficiar(y|ies)|owner)\b|\bis entitled to (the|these|all)\b"),
        "Draft asserts who is entitled to estate property. That is a determination for a court.",
    ),
    PolicyRule(
        "legal.claim_validity",
        _rx(r"\b(the claim is valid|this claim is legitimate|you are (legally )?(required|obliged) to (pay|release|transfer))\b"),
        "Draft asserts the validity of a claim or a legal obligation on the recipient.",
    ),
    PolicyRule(
        "legal.interpretation",
        _rx(r"\b(under section \d+|pursuant to [A-Z][\w. ]+ (Act|Code|Statute)|the law requires you to)\b"),
        "Draft interprets statute. Escalate to the estate's attorney instead.",
    ),
)

# Boundary 4 triggers: situations where the agent must stop and ask.
ESCALATION_TRIGGERS: tuple[PolicyRule, ...] = (
    PolicyRule(
        "escalate.legal_question_posed",
        _rx(r"\b(who is the beneficiar|confirm the beneficiar|dispute|contested|litigation|court order|subpoena)\b"),
        "The institution has raised a legal or contested question.",
    ),
    PolicyRule(
        "escalate.money_movement",
        _rx(r"\b(wire|transfer|remit|release)\b[^.\n]{0,40}\b(funds|balance|proceeds|benefit)\b"),
        "Movement of estate funds requires an explicit human decision every time.",
    ),
    PolicyRule(
        "escalate.identity_challenge",
        _rx(r"\b(unable to verify|cannot confirm your authority|your authority (is|has been) questioned|notaris|notariz)\b"),
        "The institution is challenging the executor's authority.",
    ),
    PolicyRule(
        "escalate.unexpected_liability",
        _rx(r"\b(outstanding (balance|debt|liability)|amount owed|arrears|collection|deficiency)\b"),
        "A liability has surfaced that the estate may need to contest or settle.",
    ),
)


def scan(text: str, rules: tuple[PolicyRule, ...]) -> list[dict[str, Any]]:
    hits = []
    for rule in rules:
        match = rule.pattern.search(text or "")
        if match:
            hits.append(
                {
                    "rule": rule.name,
                    "message": rule.message,
                    "excerpt": " ".join(match.group(0).split())[:140],
                }
            )
    return hits


def check_draft(packet: ClosurePacket) -> list[dict[str, Any]]:
    """Boundaries 1 and 2, applied to a draft before it reaches the approval queue."""
    text = f"{packet.subject}\n{packet.body}"
    return [*scan(text, SIGNATURE_RULES), *scan(text, LEGAL_DETERMINATION_RULES)]


def assert_draft_clean(packet: ClosurePacket) -> None:
    violations = check_draft(packet)
    if violations:
        detail = "; ".join(f"{v['rule']}: {v['excerpt']}" for v in violations)
        raise PolicyViolation(f"draft for {packet.institution_name} violates a boundary - {detail}")


def needs_escalation(
    *,
    classification_label: CorrespondenceClass | str | None = None,
    text: str = "",
    case: InstitutionCase | None = None,
    max_follow_ups: int = 2,
) -> tuple[bool, str]:
    """Boundary 4. Returns (escalate, one-line reason).

    Deliberately eager. A false escalation costs the executor thirty seconds; a missed one
    costs them a decision they never knew was made on their behalf.
    """
    label = classification_label.value if isinstance(classification_label, CorrespondenceClass) else classification_label

    if label == CorrespondenceClass.REJECTION.value:
        return True, "The institution rejected the request; how to respond is a judgement call."

    hits = scan(text, ESCALATION_TRIGGERS)
    if hits:
        return True, hits[0]["message"]

    if label == CorrespondenceClass.UNKNOWN.value:
        return True, "The response could not be classified with confidence."

    # Only for correspondence that leaves the matter open. If they have just told you
    # the account is closed, the number of chasers you sent getting there is history,
    # not a reason to put a decision in front of a grieving executor.
    still_open = label not in {
        CorrespondenceClass.COMPLETION.value,
        CorrespondenceClass.ACKNOWLEDGEMENT.value,
    }
    if still_open and case is not None and case.follow_ups_sent >= max_follow_ups:
        return True, (
            f"{case.follow_ups_sent} follow-ups have already been sent to "
            f"{case.institution_name} without resolution."
        )

    return False, ""


def assert_sendable(packet: ClosurePacket, approval: ApprovalRequest | None) -> None:
    """The gate. Invariant 1.

    Called by `services/api/transport.py` immediately before transmission, and by nothing
    else. Every condition here is a separate raise so that a failure names the exact
    reason in the audit record.
    """
    if approval is None:
        raise PolicyViolation(
            f"no approval record exists for packet {packet.id}. Every outbound "
            f"communication requires executor approval - there is no exception tier."
        )
    if approval.packet_id != packet.id:
        raise PolicyViolation(
            f"approval {approval.id} references packet {approval.packet_id}, not "
            f"{packet.id}. An approval is not transferable between drafts."
        )
    if approval.status is not ApprovalStatus.APPROVED:
        raise PolicyViolation(
            f"approval {approval.id} is {approval.status.value}, not APPROVED."
        )
    if approval.decided_by is None:
        raise PolicyViolation(f"approval {approval.id} has no human decider recorded.")
    if packet.sent_at is not None:
        raise PolicyViolation(
            f"packet {packet.id} was already sent at {packet.sent_at.isoformat()}. "
            f"Re-sending would double-count in the fiduciary record."
        )
    assert_draft_clean(packet)


def assert_state_allows_send(case: InstitutionCase) -> None:
    """Structural check at the gate: only a case sitting in AWAITING_APPROVAL may send."""
    if case.state is not CaseState.AWAITING_APPROVAL:
        raise PolicyViolation(
            f"{case.institution_name} is in {case.state.value}; only AWAITING_APPROVAL "
            f"may transition to SENT."
        )


def risk_flags(packet: ClosurePacket, disclosure_summary: dict[str, Any]) -> list[str]:
    """What the approval queue highlights for the executor.

    An executor approving twenty letters will read the flags, not the letters. So the
    flags have to be worth reading: high-sensitivity disclosures, unusual channels, and
    anything the boundary scan noticed.
    """
    flags: list[str] = []
    if disclosure_summary.get("highest_disclosed_sensitivity") in {"HIGH", "CRITICAL"}:
        flags.append(
            f"Discloses {disclosure_summary['highest_disclosed_sensitivity'].lower()}-sensitivity "
            f"material ({', '.join(disclosure_summary.get('disclosed', [])[:3])})"
        )
    if packet.channel.value == "PORTAL":
        flags.append("Submitted through a third-party portal rather than direct correspondence")
    for violation in check_draft(packet):
        flags.append(f"Boundary check: {violation['message']}")
    found = _pii_in_body(packet.body)
    if found:
        flags.append(f"Body contains {', '.join(sorted(set(found)))} - confirm this is intended")
    return flags


def _pii_in_body(body: str) -> list[str]:
    from packages.guardrails.pii import detect

    return [f.kind for f in detect(body) if f.severity in {"high", "critical"}]
