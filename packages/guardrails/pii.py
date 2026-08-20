"""Outbound PII minimizer - least disclosure, computed per recipient.

The pension fund needs a certified death certificate. The magazine needs a name and a
date. Both are asking about the same person, and the difference between what they get is
not a matter of taste - it is the whole privacy argument, and it is computed from the
recipient's own published requirements rather than from what happens to be in the file.

The rule enforced here: **a field is disclosed only if this recipient's playbook names
it.** "Send everything we have" is not reachable; there is no code path that assembles a
packet from the estate record without going through `minimize`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.core.models import DisclosureItem, Sensitivity

# --- what an estate knows ------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    name: str
    sensitivity: Sensitivity
    description: str
    redactor: str = "mask"  # mask | initials | year_only | withhold


FIELD_CATALOG: dict[str, FieldSpec] = {
    f.name: f
    for f in (
        FieldSpec("decedent_full_name", Sensitivity.LOW, "Legal name of the deceased", "initials"),
        FieldSpec("decedent_date_of_death", Sensitivity.LOW, "Date of death", "year_only"),
        FieldSpec("decedent_date_of_birth", Sensitivity.MEDIUM, "Date of birth", "year_only"),
        FieldSpec("decedent_last_address", Sensitivity.MEDIUM, "Last known address", "mask"),
        FieldSpec("decedent_ssn_last4", Sensitivity.HIGH, "Last four of the SSN", "mask"),
        FieldSpec("decedent_ssn_full", Sensitivity.CRITICAL, "Full Social Security number", "withhold"),
        FieldSpec("cause_of_death", Sensitivity.CRITICAL, "Cause of death", "withhold"),
        FieldSpec("death_certificate_certified", Sensitivity.HIGH, "Certified copy of the death certificate", "withhold"),
        FieldSpec("death_certificate_number", Sensitivity.HIGH, "Death certificate registration number", "mask"),
        FieldSpec("letters_testamentary", Sensitivity.HIGH, "Grant of probate / letters testamentary", "withhold"),
        FieldSpec("executor_full_name", Sensitivity.LOW, "Executor's name", "initials"),
        FieldSpec("executor_email", Sensitivity.LOW, "Executor's email", "mask"),
        FieldSpec("executor_phone", Sensitivity.MEDIUM, "Executor's phone", "mask"),
        FieldSpec("executor_address", Sensitivity.MEDIUM, "Executor's postal address", "mask"),
        FieldSpec("executor_photo_id", Sensitivity.HIGH, "Executor's government photo ID", "withhold"),
        FieldSpec("account_fingerprint", Sensitivity.MEDIUM, "Masked account reference", "mask"),
        FieldSpec("account_number_full", Sensitivity.CRITICAL, "Full account number", "withhold"),
        FieldSpec("policy_number", Sensitivity.MEDIUM, "Policy or member number", "mask"),
        FieldSpec("estate_value", Sensitivity.HIGH, "Total estate value", "withhold"),
        FieldSpec("beneficiary_names", Sensitivity.HIGH, "Names of other beneficiaries", "withhold"),
        FieldSpec("tax_identification_number", Sensitivity.CRITICAL, "Estate TIN", "withhold"),
    )
}

# No institution's account-closure process needs these, and no playbook may request them.
# A request for one is not a disclosure decision - it escalates to the executor.
NEVER_DISCLOSE = {"cause_of_death", "decedent_ssn_full", "account_number_full"}

# Fields every recipient gets: without them the letter is not about anybody.
ALWAYS_MINIMUM = ("decedent_full_name", "decedent_date_of_death", "executor_full_name")


# --- detection -----------------------------------------------------------------------


@dataclass
class PIIFinding:
    kind: str
    value: str
    start: int
    end: int
    severity: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "start": self.start,
            "end": self.end,
            "severity": self.severity,
        }


DETECTORS: list[tuple[str, re.Pattern[str], str]] = [
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "critical"),
    ("US_SSN", re.compile(r"\bSSN\s*[:#]?\s*\d{9}\b", re.IGNORECASE), "critical"),
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "critical"),
    ("BANK_ACCOUNT", re.compile(r"\b(?:account|acct)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{8,17})\b", re.IGNORECASE), "critical"),
    ("ROUTING_NUMBER", re.compile(r"\b(?:routing|aba)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{9})\b", re.IGNORECASE), "critical"),
    ("DATE_OF_BIRTH", re.compile(r"\b(?:date of birth|d\.?o\.?b\.?|born)\s*[:\-]?\s*\d{4}-\d{2}-\d{2}\b", re.IGNORECASE), "high"),
    ("PHONE", re.compile(r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ -]\d{3}[ -]\d{4}\b"), "medium"),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "low"),
    ("STREET_ADDRESS", re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way)\b"), "medium"),
    ("MEDICAL", re.compile(r"\b(cause of death|myocardial|carcinoma|dementia|terminal|diagnosis)\b", re.IGNORECASE), "critical"),
]


def detect(text: str) -> list[PIIFinding]:
    """Find PII in arbitrary text. Used to inspect a draft before it reaches a human."""
    findings: list[PIIFinding] = []
    seen: set[tuple[int, int]] = set()
    for kind, pattern, severity in DETECTORS:
        for match in pattern.finditer(text or ""):
            span = (match.start(), match.end())
            if span in seen:
                continue
            seen.add(span)
            findings.append(
                PIIFinding(kind=kind, value=match.group(0), start=span[0], end=span[1], severity=severity)
            )
    return sorted(findings, key=lambda f: f.start)


# --- redaction -----------------------------------------------------------------------


def redact(value: str, style: str) -> str:
    if value is None:
        return "[not held]"
    value = str(value)
    if style == "withhold":
        return "[withheld - not required by this recipient]"
    if style == "initials":
        parts = [p for p in re.split(r"\s+", value) if p]
        return " ".join(f"{p[0]}." for p in parts) if parts else "[withheld]"
    if style == "year_only":
        match = re.search(r"\b(\d{4})\b", value)
        return f"{match.group(1)} (year only)" if match else "[withheld]"
    # mask
    stripped = value.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return f"{stripped[:2]}{'*' * max(3, len(stripped) - 4)}{stripped[-2:]}"


# --- the minimizer -------------------------------------------------------------------


class DisclosureRefused(RuntimeError):
    """A playbook asked for a field on the never-disclose list."""

    def __init__(self, field: str, recipient: str) -> None:
        super().__init__(
            f"{recipient} requires '{field}', which is on the never-disclose list. This is "
            f"a judgement for the executor, not a redaction decision - escalate."
        )
        self.field = field
        self.recipient = recipient


def minimize(
    facts: dict[str, Any],
    *,
    required: list[str],
    recipient: str,
    playbook_name: str = "",
) -> list[DisclosureItem]:
    """Compute what this recipient gets.

    `required` comes from the playbook's `required_disclosures`. Everything the estate
    holds appears in the result - withheld items included - because the executor
    approving this needs to see what was *not* sent as much as what was.
    """
    wanted = set(required) | set(ALWAYS_MINIMUM)

    for field in required:
        if field in NEVER_DISCLOSE:
            raise DisclosureRefused(field, recipient)

    items: list[DisclosureItem] = []
    for name, spec in FIELD_CATALOG.items():
        held = facts.get(name)
        if held is None and name not in wanted:
            continue  # the estate does not hold it and nobody asked; not worth a row

        disclose = name in wanted and held is not None and name not in NEVER_DISCLOSE
        if disclose:
            justification = (
                f"Named in {playbook_name or recipient}'s required disclosures."
                if name in required
                else "Minimum identification - the letter is unusable without it."
            )
        elif name in NEVER_DISCLOSE:
            justification = "On the never-disclose list; no closure process requires it."
        elif held is None:
            justification = "The estate does not hold this."
        else:
            justification = f"Held, but not required by {recipient}'s published process."

        items.append(
            DisclosureItem(
                field=name,
                sensitivity=spec.sensitivity,
                value=str(held) if held is not None else None,
                redacted_value=redact(str(held), spec.redactor) if held is not None else "[not held]",
                disclosed=disclose,
                justification=justification,
                required_by=playbook_name if name in required else None,
            )
        )

    return sorted(items, key=lambda item: (not item.disclosed, item.field))


def scrub(body: str, disclosures: list[DisclosureItem]) -> tuple[str, list[str]]:
    """Defence in depth: remove any withheld value that leaked into the drafted body.

    The drafting step is supposed to work only from disclosed fields. This assumes it
    might not - a model paraphrasing an address it was shown for context is a realistic
    failure, and one that would otherwise be invisible until it was already sent.
    """
    removed: list[str] = []
    scrubbed = body
    # A value that some other field discloses legitimately is not a leak. Without this,
    # two fields holding the same identifier would make the scrubber delete the one the
    # recipient is entitled to see.
    disclosed_values = {
        str(item.value).strip().lower() for item in disclosures if item.disclosed and item.value
    }
    for item in disclosures:
        if item.disclosed or not item.value:
            continue
        if str(item.value).strip().lower() in disclosed_values:
            continue
        value = str(item.value).strip()
        if len(value) < 4:
            continue
        if value.lower() in scrubbed.lower():
            pattern = re.compile(re.escape(value), re.IGNORECASE)
            scrubbed = pattern.sub(f"[{item.field} withheld]", scrubbed)
            removed.append(item.field)
    return scrubbed, removed


def diff_view(left: list[DisclosureItem], right: list[DisclosureItem]) -> list[dict[str, Any]]:
    """The side-by-side the demo shows at 2:15.

    One row per field, what each recipient gets. The interesting rows are the ones that
    differ, so they sort first.
    """
    fields = sorted({item.field for item in [*left, *right]})
    left_by, right_by = {i.field: i for i in left}, {i.field: i for i in right}
    rows = []
    for field in fields:
        a, b = left_by.get(field), right_by.get(field)
        rows.append(
            {
                "field": field,
                "sensitivity": (a or b).sensitivity.value if (a or b) else "LOW",  # type: ignore[union-attr]
                "left": a.shown if a else "[not applicable]",
                "left_disclosed": bool(a and a.disclosed),
                "right": b.shown if b else "[not applicable]",
                "right_disclosed": bool(b and b.disclosed),
                "differs": bool(a and b and a.disclosed != b.disclosed),
            }
        )
    rows.sort(key=lambda row: (not row["differs"], row["field"]))
    return rows


def summarize(items: list[DisclosureItem]) -> dict[str, Any]:
    disclosed = [i for i in items if i.disclosed]
    withheld = [i for i in items if not i.disclosed]
    return {
        "disclosed_count": len(disclosed),
        "withheld_count": len(withheld),
        "disclosed": [i.field for i in disclosed],
        "withheld": [i.field for i in withheld],
        "highest_disclosed_sensitivity": max(
            (i.sensitivity.value for i in disclosed),
            key=lambda s: ["PUBLIC", "LOW", "MEDIUM", "HIGH", "CRITICAL"].index(s),
            default="PUBLIC",
        ),
    }
