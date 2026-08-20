"""Playbook schema.

A playbook is one institution's closure process, encoded once and reused by every estate
that touches that institution. It is the compounding asset in this submission: the
obligation graph is rebuilt per estate, but the playbook only gets better.

The validator is the interesting part. `required_disclosures` must name fields that exist
in the PII catalog, and may never name a field on the never-disclose list - so a playbook
literally cannot be written that asks the agent to hand over a cause of death. The
disclosure boundary is enforced at the schema, not at the call site.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.core.models import Channel, ObligationCategory
from packages.guardrails.pii import FIELD_CATALOG, NEVER_DISCLOSE


class FollowUpDemand(BaseModel):
    """Something an institution asked for that the first letter did not include.

    Recorded so the next estate encloses it up front. `first_seen` is a version string:
    it tells you which amendment introduced the knowledge.
    """

    document: str
    frequency: str = "occasional"  # always | common | occasional | rare
    first_seen: str = "1.0.0"
    note: str = ""


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Registry slug, kebab-case, stable across versions")
    institution_name: str
    category: ObligationCategory
    version: str = "1.0.0"
    aliases: list[str] = Field(
        default_factory=list,
        description="How this institution appears on statements and debit lines",
    )

    department: str = "Bereavement Services"
    submission_channel: Channel = Channel.EMAIL
    submission_address: str = ""
    submission_note: str = ""

    required_documents: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)

    typical_sla_days: int = 14
    follow_up_after_days: int = 21
    max_follow_ups: int = 2

    known_follow_up_demands: list[FollowUpDemand] = Field(default_factory=list)
    escalation_triggers: list[str] = Field(default_factory=list)
    closure_signals: list[str] = Field(default_factory=list)

    # Provenance. A playbook nobody can trace is a playbook nobody should trust.
    source: str = ""
    notes: str = ""
    # Invariant 6: every institution in this repo is invented.
    fictional: bool = True

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not value or any(ch.isupper() or ch == " " for ch in value):
            raise ValueError(f"playbook name '{value}' must be lower-case kebab-case")
        return value

    @field_validator("required_disclosures")
    @classmethod
    def _known_fields(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in FIELD_CATALOG]
        if unknown:
            raise ValueError(
                f"required_disclosures names fields absent from the PII catalog: {unknown}. "
                f"Add them to packages/guardrails/pii.py with a sensitivity first."
            )
        forbidden = [v for v in values if v in NEVER_DISCLOSE]
        if forbidden:
            raise ValueError(
                f"required_disclosures may never contain {forbidden} - these are on the "
                f"never-disclose list. If an institution genuinely demands one, that is an "
                f"escalation to the executor, not a playbook change."
            )
        return values

    @model_validator(mode="after")
    def _channel_has_address(self) -> Playbook:
        if self.submission_channel in {Channel.EMAIL, Channel.EFAX} and not self.submission_address:
            raise ValueError(f"{self.name}: {self.submission_channel.value} needs a submission_address")
        return self

    # --- helpers used by the orchestrator -------------------------------------------

    def matches(self, institution_name: str) -> bool:
        from packages.core.offline import _same_institution

        candidates = [self.institution_name, *self.aliases]
        return any(_same_institution(institution_name, candidate) for candidate in candidates)

    def to_document(self) -> dict[str, Any]:
        """What gets published to the registry."""
        return self.model_dump(mode="json")

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Playbook:
        payload = {k: v for k, v in document.items() if k != "published_at"}
        return cls.model_validate(payload)

    def draft_context(self) -> dict[str, Any]:
        """The subset the drafting task sees. Deliberately not the whole playbook."""
        return {
            "name": self.name,
            "version": self.version,
            "department": self.department,
            "required_documents": self.required_documents,
            "submission_note": self.submission_note,
            "typical_sla_days": self.typical_sla_days,
        }


GENERIC_NAME = "generic-closure"


def generic_playbook(category: ObligationCategory, institution_name: str) -> Playbook:
    """The long tail.

    README section 10 is honest that six institutions are covered in depth and everything
    else falls back to this plus human review. A generic playbook that pretends to know
    an institution's process would be worse than one that admits it does not.
    """
    return Playbook(
        name=GENERIC_NAME,
        institution_name=institution_name,
        category=category,
        version="1.0.0",
        department="Bereavement / Estates",
        submission_channel=Channel.EMAIL,
        submission_address="bereavement@institution.example.invalid",
        submission_note=(
            "This letter follows a generic closure template because no institution-specific "
            "playbook is published for this recipient. The executor should expect one "
            "additional round trip while the institution states its requirements."
        ),
        required_documents=[
            "certified copy of the death certificate",
            "executor's proof of appointment",
        ],
        required_disclosures=[
            "decedent_full_name",
            "decedent_date_of_death",
            "executor_full_name",
            "executor_email",
            "account_fingerprint",
        ],
        typical_sla_days=21,
        follow_up_after_days=28,
        escalation_triggers=[
            "any request for documentation beyond the death certificate and proof of appointment",
            "any mention of an outstanding balance",
        ],
        closure_signals=["account closed", "no further action required"],
        notes="Fallback template. Every use of it is a candidate for a real playbook.",
        fictional=True,
    )
