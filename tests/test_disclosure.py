"""The PII minimizer: least disclosure, computed per recipient.

The demo beat at 2:15 is a side-by-side - the pension fund gets a certified death
certificate, the wine society gets a name and a date. These tests are what make that beat
true rather than staged.
"""

from __future__ import annotations

import pytest

from packages.core.models import Sensitivity
from packages.guardrails.pii import (
    ALWAYS_MINIMUM,
    FIELD_CATALOG,
    NEVER_DISCLOSE,
    DisclosureRefused,
    detect,
    diff_view,
    minimize,
    redact,
    scrub,
    summarize,
)

FACTS = {
    "decedent_full_name": "Eleanor Margaret Halloran",
    "decedent_date_of_death": "2026-06-14",
    "decedent_date_of_birth": "1948-03-11",
    "decedent_last_address": "218 Ridgeline Avenue, Oakland CA 94611",
    "decedent_ssn_last4": "4417",
    "death_certificate_certified": "certified copy enclosed",
    "letters_testamentary": "issued CA probate, ref PR-2026-04482",
    "executor_full_name": "Daniel R. Halloran",
    "executor_email": "daniel.halloran@example.invalid",
    "executor_photo_id": "government-issued photo ID enclosed",
    "account_fingerprint": "****3391:9f2c1a04",
}

PENSION_REQUIRES = [
    "decedent_full_name",
    "decedent_date_of_death",
    "decedent_date_of_birth",
    "decedent_last_address",
    "decedent_ssn_last4",
    "death_certificate_certified",
    "letters_testamentary",
    "executor_full_name",
    "executor_email",
]

SUBSCRIPTION_REQUIRES = [
    "decedent_full_name",
    "decedent_date_of_death",
    "executor_full_name",
    "executor_email",
]


def test_a_recipient_gets_only_what_its_playbook_names():
    items = minimize(
        FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Blue Heron Wine Society",
        playbook_name="generic-closure",
    )
    disclosed = {i.field for i in items if i.disclosed}
    assert disclosed == set(SUBSCRIPTION_REQUIRES) | set(ALWAYS_MINIMUM)
    assert "death_certificate_certified" not in disclosed
    assert "decedent_ssn_last4" not in disclosed
    assert "decedent_last_address" not in disclosed


def test_the_pension_fund_legitimately_gets_more():
    pension = minimize(FACTS, required=PENSION_REQUIRES, recipient="Ironbridge", playbook_name="ironbridge")
    magazine = minimize(FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Thornfield", playbook_name="generic")

    pension_fields = {i.field for i in pension if i.disclosed}
    magazine_fields = {i.field for i in magazine if i.disclosed}

    assert magazine_fields < pension_fields, "the magazine must get strictly less"
    assert "death_certificate_certified" in pension_fields
    assert "letters_testamentary" in pension_fields


def test_withheld_fields_are_still_reported_to_the_executor():
    """The approval view has to show what was *not* sent, or it is not a review."""
    items = minimize(FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Thornfield")
    withheld = [i for i in items if not i.disclosed]
    assert withheld, "expected some fields to be withheld"
    assert all(i.justification for i in withheld), "every withheld field needs a reason"
    assert all(i.redacted_value for i in withheld)


def test_every_disclosure_carries_a_justification():
    items = minimize(FACTS, required=PENSION_REQUIRES, recipient="Ironbridge", playbook_name="ironbridge")
    for item in items:
        assert item.justification, f"{item.field} has no justification"


@pytest.mark.parametrize("field", sorted(NEVER_DISCLOSE))
def test_a_never_disclose_field_is_refused_not_redacted(field):
    """Refused, not quietly redacted.

    A playbook asking for a cause of death is not a disclosure decision the agent gets to
    make more carefully - it is a signal that something is wrong, and it escalates.
    """
    with pytest.raises(DisclosureRefused) as excinfo:
        minimize(FACTS, required=["decedent_full_name", field], recipient="Greedy Institution")
    assert field in str(excinfo.value)
    assert "escalate" in str(excinfo.value)


def test_never_disclose_fields_are_never_disclosed_even_if_held():
    facts = {**FACTS, "cause_of_death": "recorded on the registrar's copy"}
    items = minimize(facts, required=PENSION_REQUIRES, recipient="Ironbridge")
    cause = next(i for i in items if i.field == "cause_of_death")
    assert cause.disclosed is False
    assert "never-disclose" in cause.justification


def test_the_minimum_identifying_set_is_always_present():
    items = minimize(FACTS, required=[], recipient="Anyone")
    disclosed = {i.field for i in items if i.disclosed}
    assert set(ALWAYS_MINIMUM) <= disclosed, "a letter about nobody is not a letter"


# --- redaction -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    [
        ("Eleanor Margaret Halloran", "initials", "E. M. H."),
        ("2026-06-14", "year_only", "2026 (year only)"),
        ("anything at all", "withhold", "[withheld - not required by this recipient]"),
    ],
)
def test_redaction_styles(value, style, expected):
    assert redact(value, style) == expected


def test_masking_keeps_the_ends_and_hides_the_middle():
    masked = redact("218 Ridgeline Avenue, Oakland CA 94611", "mask")
    assert masked.startswith("21")
    assert masked.endswith("11")
    assert "Ridgeline" not in masked


# --- the scrubber --------------------------------------------------------------------


def test_scrub_removes_a_withheld_value_that_leaked_into_the_body():
    items = minimize(FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Thornfield")
    body = (
        "Dear Sir or Madam, the deceased lived at "
        "218 Ridgeline Avenue, Oakland CA 94611 and I enclose the certificate."
    )
    cleaned, removed = scrub(body, items)
    assert "Ridgeline" not in cleaned
    assert "decedent_last_address" in removed


def test_scrub_leaves_a_disclosed_value_alone():
    items = minimize(FACTS, required=PENSION_REQUIRES, recipient="Ironbridge")
    body = "The deceased lived at 218 Ridgeline Avenue, Oakland CA 94611."
    cleaned, removed = scrub(body, items)
    assert "Ridgeline" in cleaned
    assert removed == []


def test_scrub_does_not_redact_a_value_another_field_discloses():
    """Two fields can hold the same identifier. Withholding one must not delete the other."""
    facts = {**FACTS, "policy_number": FACTS["account_fingerprint"]}
    items = minimize(
        facts,
        required=["decedent_full_name", "account_fingerprint"],
        recipient="Meridian Trust Bank",
    )
    body = f"The account is identified in your records as {FACTS['account_fingerprint']}."
    cleaned, removed = scrub(body, items)
    assert FACTS["account_fingerprint"] in cleaned
    assert removed == []


# --- the diff view -------------------------------------------------------------------


def test_diff_view_puts_the_differences_first():
    pension = minimize(FACTS, required=PENSION_REQUIRES, recipient="Ironbridge")
    magazine = minimize(FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Thornfield")
    rows = diff_view(pension, magazine)

    assert rows[0]["differs"] is True, "the interesting rows sort first"
    differing = [r for r in rows if r["differs"]]
    assert any(r["field"] == "death_certificate_certified" for r in differing)
    for row in differing:
        assert row["left_disclosed"] != row["right_disclosed"]


def test_summarize_reports_the_highest_sensitivity_disclosed():
    pension = minimize(FACTS, required=PENSION_REQUIRES, recipient="Ironbridge")
    magazine = minimize(FACTS, required=SUBSCRIPTION_REQUIRES, recipient="Thornfield")

    assert summarize(pension)["highest_disclosed_sensitivity"] == "HIGH"
    assert summarize(magazine)["highest_disclosed_sensitivity"] == "LOW"
    assert summarize(magazine)["disclosed_count"] < summarize(pension)["disclosed_count"]


# --- detection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("SSN 123-45-6789 on file", "US_SSN"),
        ("Card 4111 1111 1111 1111", "CREDIT_CARD"),
        ("account number: 88711920431", "BANK_ACCOUNT"),
        ("routing number 121000248", "ROUTING_NUMBER"),
        ("date of birth: 1948-03-11", "DATE_OF_BIRTH"),
        ("cause of death was recorded", "MEDICAL"),
    ],
)
def test_detector_finds_high_risk_content(text, kind):
    assert kind in {f.kind for f in detect(text)}


def test_detector_is_quiet_on_an_ordinary_letter():
    body = (
        "I am writing in my capacity as executor of the estate. I am asking you to "
        "record the death and tell me what your process requires from me."
    )
    assert [f for f in detect(body) if f.severity in {"high", "critical"}] == []


def test_the_field_catalog_covers_every_sensitivity_level():
    levels = {spec.sensitivity for spec in FIELD_CATALOG.values()}
    assert Sensitivity.CRITICAL in levels
    assert Sensitivity.LOW in levels
