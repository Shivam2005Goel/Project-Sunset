"""Domain models - the contract between every service.

Change the model first, then the callers. Pydantic v2 throughout.

Naming note: an *obligation* is a discovered relationship between the estate and an
institution. A *case* is the work of closing it. One obligation, one case, one sub-agent.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.core.clock import now


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(BaseModel):
    model_config = ConfigDict(use_enum_values=False, validate_assignment=True)


# --- enums ---------------------------------------------------------------------------


class CaseState(str, Enum):
    """The explicit state machine. See `packages/core/fsm.py` for legal transitions."""

    DISCOVERED = "DISCOVERED"
    PACKET_DRAFTED = "PACKET_DRAFTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SENT = "SENT"
    AWAITING_RESPONSE = "AWAITING_RESPONSE"
    INFO_REQUESTED = "INFO_REQUESTED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


TERMINAL_STATES = {CaseState.CLOSED}


class ObligationCategory(str, Enum):
    BANK = "BANK"
    BROKERAGE = "BROKERAGE"
    LIFE_INSURANCE = "LIFE_INSURANCE"
    PENSION = "PENSION"
    UTILITY = "UTILITY"
    TELECOM = "TELECOM"
    SUBSCRIPTION = "SUBSCRIPTION"
    CREDIT_CARD = "CREDIT_CARD"
    MORTGAGE = "MORTGAGE"
    GOVERNMENT = "GOVERNMENT"
    UNCLAIMED_PROPERTY = "UNCLAIMED_PROPERTY"
    OTHER = "OTHER"


class DiscoveryMethod(str, Enum):
    """How the obligation was found. The demo's surprise lives in INFERENCE and REGISTRY."""

    DOCUMENT = "DOCUMENT"  # named on an uploaded statement or letterhead
    INFERENCE = "INFERENCE"  # deduced from a recurring debit with no matching statement
    REGISTRY = "REGISTRY"  # state unclaimed-property database match
    EXECUTOR = "EXECUTOR"  # the family listed it


class Channel(str, Enum):
    EMAIL = "EMAIL"
    POSTAL = "POSTAL"
    EFAX = "EFAX"
    PORTAL = "PORTAL"


class CorrespondenceClass(str, Enum):
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
    REJECTION = "REJECTION"
    COMPLETION = "COMPLETION"
    IRRELEVANT = "IRRELEVANT"
    UNKNOWN = "UNKNOWN"


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    SANITIZE = "SANITIZE"
    BLOCK = "BLOCK"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalKind(str, Enum):
    OUTBOUND = "OUTBOUND"
    ESCALATION = "ESCALATION"
    PLAYBOOK_AMENDMENT = "PLAYBOOK_AMENDMENT"


class Sensitivity(str, Enum):
    """Drives the PII minimizer. PUBLIC is safe to disclose to anyone who asks."""

    PUBLIC = "PUBLIC"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# --- estate --------------------------------------------------------------------------


class Decedent(Base):
    full_name: str
    date_of_birth: str
    date_of_death: str
    last_address: str
    ssn_last4: str | None = None

    @property
    def display(self) -> str:
        return f"{self.full_name} ({self.date_of_birth} - {self.date_of_death})"


class Executor(Base):
    full_name: str
    email: str
    relationship: str = "executor"
    grant_reference: str | None = None


class Estate(Base):
    id: str = Field(default_factory=lambda: _id("est"))
    decedent: Decedent
    executor: Executor
    created_at: datetime = Field(default_factory=now)
    jurisdiction: str = "CA"
    # Invariant 6. Every estate in this repo is fabricated; the flag exists so the UI can
    # say so on screen without anyone having to remember to add the banner.
    fictional: bool = True
    notes: str | None = None


# --- discovery -----------------------------------------------------------------------


class Evidence(Base):
    """A pointer back to the page the claim came from. No evidence, no obligation."""

    source_document: str
    page: int | None = None
    excerpt: str = ""
    kind: Literal["letterhead", "debit_line", "policy_number", "footer", "registry_match", "executor_statement"] = "debit_line"

    def short(self) -> str:
        page = f" p.{self.page}" if self.page else ""
        return f"{self.source_document}{page}"


class Obligation(Base):
    id: str = Field(default_factory=lambda: _id("obl"))
    estate_id: str
    institution_id: str
    institution_name: str
    category: ObligationCategory
    # Never the full number. A fingerprint is enough to match correspondence and not
    # enough to leak an account.
    account_fingerprint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    discovery_method: DiscoveryMethod = DiscoveryMethod.DOCUMENT
    evidence: list[Evidence] = Field(default_factory=list)
    estimated_value_usd: float | None = None
    recurring_amount_usd: float | None = None
    contact_email: str | None = None
    discovered_at: datetime = Field(default_factory=now)
    notes: str | None = None

    @property
    def is_surprise(self) -> bool:
        """Found by the agent, not handed to it. This is the Day 2 demo beat."""
        return self.discovery_method in {DiscoveryMethod.INFERENCE, DiscoveryMethod.REGISTRY}

    @staticmethod
    def fingerprint(account_number: str) -> str:
        """Last four plus a salted digest - stable across documents, useless if leaked."""
        digits = "".join(ch for ch in account_number if ch.isdigit())
        tail = digits[-4:] if len(digits) >= 4 else digits
        digest = hashlib.sha256(f"aftercare:{digits}".encode()).hexdigest()[:8]
        return f"****{tail}:{digest}"


# --- playbook reference --------------------------------------------------------------


class PlaybookRef(Base):
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def parse(cls, ref: str) -> PlaybookRef:
        name, _, version = ref.partition("@")
        return cls(name=name, version=version or "latest")


# --- disclosure ----------------------------------------------------------------------


class DisclosureItem(Base):
    """One field, and the argument for why this recipient gets it.

    `justification` is not decoration - it is what the executor reads in the approval
    queue, and what ends up in the audit record if anyone later asks why a pension fund
    received a full death certificate.
    """

    field: str
    sensitivity: Sensitivity
    value: str | None = None
    redacted_value: str | None = None
    disclosed: bool = True
    justification: str = ""
    required_by: str | None = None  # playbook clause that demanded it

    @property
    def shown(self) -> str:
        return (self.value or "") if self.disclosed else (self.redacted_value or "[redacted]")


class ClosurePacket(Base):
    id: str = Field(default_factory=lambda: _id("pkt"))
    estate_id: str
    case_id: str
    institution_name: str
    recipient: str
    channel: Channel = Channel.EMAIL
    subject: str = ""
    body: str = ""
    disclosures: list[DisclosureItem] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    playbook_ref: str = ""
    drafted_at: datetime = Field(default_factory=now)
    model_used: str = ""
    reasoning: str = ""
    # Set only by services/api/transport.py, and only after an approval record exists.
    sent_at: datetime | None = None
    approval_id: str | None = None

    @property
    def disclosed_fields(self) -> list[str]:
        return [d.field for d in self.disclosures if d.disclosed]

    @property
    def withheld_fields(self) -> list[str]:
        return [d.field for d in self.disclosures if not d.disclosed]


# --- approval ------------------------------------------------------------------------


class ApprovalRequest(Base):
    id: str = Field(default_factory=lambda: _id("apr"))
    estate_id: str
    case_id: str
    kind: ApprovalKind = ApprovalKind.OUTBOUND
    packet_id: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    summary: str = ""
    brief: str = ""  # the one-paragraph escalation brief, boundary 4
    risk_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None

    @property
    def is_decided(self) -> bool:
        return self.status is not ApprovalStatus.PENDING


# --- inbound -------------------------------------------------------------------------


class ScreenFinding(Base):
    rule: str
    severity: Literal["low", "medium", "high", "critical"]
    layer: Literal["text", "ocr", "attachment", "header", "encoding"]
    excerpt: str = ""
    note: str = ""


class ScreenResult(Base):
    verdict: Verdict = Verdict.ALLOW
    findings: list[ScreenFinding] = Field(default_factory=list)
    # What the classifier is allowed to see. Never the raw body.
    sanitized_text: str = ""
    screened_by: str = "fallback"
    screened_at: datetime = Field(default_factory=now)

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def max_severity(self) -> str:
        order = ["low", "medium", "high", "critical"]
        if not self.findings:
            return "none"
        return max((f.severity for f in self.findings), key=order.index)


class Classification(Base):
    label: CorrespondenceClass = CorrespondenceClass.UNKNOWN
    confidence: float = 0.0
    requested_documents: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    deadline: str | None = None
    reasoning: str = ""
    model_used: str = ""


class InboundMessage(Base):
    id: str = Field(default_factory=lambda: _id("in"))
    estate_id: str
    case_id: str | None = None
    institution_id: str | None = None
    from_address: str = ""
    subject: str = ""
    source: Literal["EMAIL", "SCAN", "PORTAL"] = "EMAIL"
    # Raw body lives in the blob store. Holding it in a field invites someone to
    # f-string it into a prompt; invariant 3 says that never happens.
    raw_ref: str = ""
    received_at: datetime = Field(default_factory=now)
    screening: ScreenResult | None = None
    classification: Classification | None = None
    handled: bool = False
    handling_note: str = ""


# --- case ----------------------------------------------------------------------------


class Transition(Base):
    from_state: CaseState
    to_state: CaseState
    event: str
    at: datetime = Field(default_factory=now)
    actor: str = "orchestrator"
    # The reasoning chain. Invariant 5: no silent transitions.
    reason: str = ""
    audit_id: str | None = None


class InstitutionCase(Base):
    id: str = Field(default_factory=lambda: _id("case"))
    estate_id: str
    obligation_id: str
    institution_id: str
    institution_name: str
    category: ObligationCategory
    state: CaseState = CaseState.DISCOVERED
    playbook_ref: str | None = None
    packet_id: str | None = None
    approval_id: str | None = None
    history: list[Transition] = Field(default_factory=list)
    opened_at: datetime = Field(default_factory=now)
    closed_at: datetime | None = None
    # When the sub-agent should wake up if nothing arrives. Dormant until then - it holds
    # no CPU, which is the whole Agent Runtime argument.
    next_wake_at: datetime | None = None
    follow_ups_sent: int = 0
    escalation_brief: str | None = None
    recovered_amount_usd: float = 0.0
    memory_key: str = ""
    outstanding_requests: list[str] = Field(default_factory=list)
    amendment_proposed: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_open(self) -> bool:
        return not self.is_terminal and self.state is not CaseState.ESCALATED


# --- audit ---------------------------------------------------------------------------


class AuditRecord(Base):
    """One immutable line of the fiduciary record.

    Hash-chained: `digest = H(seq, prev_digest, canonical_payload)`. Rewriting history
    means recomputing every subsequent digest, which is the property that makes this
    defensible in front of a probate court rather than merely tidy.
    """

    id: str = Field(default_factory=lambda: _id("aud"))
    seq: int = 0
    at: datetime = Field(default_factory=now)
    estate_id: str
    institution_id: str | None = None
    case_id: str | None = None
    actor: str = "system"
    action: str = ""
    reasoning: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    prev_digest: str = ""
    digest: str = ""

    def compute_digest(self) -> str:
        import json

        canonical = json.dumps(
            {
                "seq": self.seq,
                "at": self.at.isoformat(),
                "estate_id": self.estate_id,
                "institution_id": self.institution_id,
                "case_id": self.case_id,
                "actor": self.actor,
                "action": self.action,
                "reasoning": self.reasoning,
                "payload": self.payload,
                "prev_digest": self.prev_digest,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


# --- fleet bookkeeping ---------------------------------------------------------------


class EstateSummary(Base):
    """What the dashboard header shows. Computed, never stored."""

    estate_id: str
    decedent_name: str
    discovered: int = 0
    surprises: int = 0
    closed: int = 0
    escalated: int = 0
    pending_approval: int = 0
    in_flight: int = 0
    recovered_usd: float = 0.0
    injections_blocked: int = 0
    simulated_date: str | None = None
    by_state: dict[str, int] = Field(default_factory=dict)
