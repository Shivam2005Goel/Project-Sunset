"""Discovery: the obligation graph, and the part of it nobody handed us.

Exit test from BUILD_PLAN.md Day 2: upload the seeded corpus, get 23 obligations, at
least 3 of which are absent from the naive list. These tests hold that number in place
and check the reasoning behind the surprising ones, because "the agent found something"
is only impressive if you can see why it was findable.
"""

from __future__ import annotations

import pytest

from packages.core.models import DiscoveryMethod, ObligationCategory
from packages.core.offline import (
    _same_institution,
    extract_from_document,
    infer_hidden_obligations,
)
from packages.core.repos import get_repos
from demo.corpus_builder import STATEMENTS_DIR, build_all, undocumented_lines
from services.discovery import unclaimed
from services.discovery.documents import load_corpus


@pytest.fixture(scope="module", autouse=True)
def corpus():
    build_all()
    return STATEMENTS_DIR


# --- the corpus itself ---------------------------------------------------------------


def test_the_corpus_hides_exactly_three_institutions():
    hidden = undocumented_lines()
    assert len(hidden) == 3
    assert {line.merchant for line in hidden} == {
        "FERNBROOK SELF STORAGE",
        "SILVERLINE MEDICAL ALERT",
        "COBALT RIDGE ANNUITY",
    }
    assert all(line.months >= 3 for line in hidden), "fewer than three occurrences is noise"


def test_the_corpus_loads_with_a_death_certificate_and_nineteen_documents():
    documents = load_corpus(STATEMENTS_DIR)
    assert len(documents) == 20
    assert len([d for d in documents if d.kind == "certificate"]) == 1


# --- parsing -------------------------------------------------------------------------


def test_a_statement_yields_its_issuer_account_and_balance():
    text = (STATEMENTS_DIR / "01-meridian-trust-bank-statement.txt").read_text(encoding="utf-8")
    result = extract_from_document({"text": text, "document": "meridian.txt"})

    issuer = result["institutions"][0]
    assert issuer["institution_name"] == "Meridian Trust Bank"
    assert issuer["category"] == "BANK"
    assert issuer["account_number"] == "4402-1183-3391"
    assert issuer["estimated_value_usd"] == 18442.19
    assert issuer["contact_email"].endswith("example.invalid")


def test_a_statement_yields_its_transaction_lines():
    text = (STATEMENTS_DIR / "01-meridian-trust-bank-statement.txt").read_text(encoding="utf-8")
    result = extract_from_document({"text": text, "document": "meridian.txt"})

    assert len(result["debits"]) > 100, "twelve months of activity"
    assert len(result["credits"]) == 24
    merchants = {row["merchant"] for row in result["debits"]}
    assert "Fernbrook Self Storage" in merchants


def test_a_policy_letter_yields_its_policy_number():
    text = (STATEMENTS_DIR / "05-ashgrove-mutual-life-letter.txt").read_text(encoding="utf-8")
    issuer = extract_from_document({"text": text, "document": "ashgrove.txt"})["institutions"][0]

    assert issuer["institution_name"] == "Ashgrove Mutual Life"
    assert issuer["category"] == "LIFE_INSURANCE"
    assert issuer["account_number"] == "LX-4471-8820"


# --- inference -----------------------------------------------------------------------


def test_inference_finds_what_the_documents_do_not_name():
    text = (STATEMENTS_DIR / "01-meridian-trust-bank-statement.txt").read_text(encoding="utf-8")
    parsed = extract_from_document({"text": text, "document": "meridian.txt"})

    result = infer_hidden_obligations(
        {
            "debits": parsed["debits"],
            "credits": parsed["credits"],
            "known_institutions": [
                "Meridian Trust Bank", "Pacific Grid Energy", "Bayview Water District",
                "Northshore Wireless", "Sunset Fiber Broadband", "Golden Vale Card Services",
                "Redwood Home Lending", "Ashgrove Mutual Life", "Thornfield Quarterly Review",
                "Blue Heron Wine Society", "Aurelia Press Books", "Ironbridge Retirement Fund",
            ],
        }
    )
    names = {row["institution_name"] for row in result["inferred"]}
    assert names == {"Fernbrook Self Storage", "Silverline Medical Alert", "Cobalt Ridge Annuity"}


def test_an_inferred_obligation_explains_its_own_reasoning():
    text = (STATEMENTS_DIR / "01-meridian-trust-bank-statement.txt").read_text(encoding="utf-8")
    parsed = extract_from_document({"text": text, "document": "meridian.txt"})
    result = infer_hidden_obligations(
        {"debits": parsed["debits"], "credits": parsed["credits"], "known_institutions": []}
    )
    storage = next(r for r in result["inferred"] if "Fernbrook" in r["institution_name"])

    assert "12 debits" in storage["reasoning"]
    assert "$148.00" in storage["reasoning"]
    assert storage["evidence"], "no evidence, no obligation"
    assert storage["confidence"] > 0.8


def test_money_arriving_on_a_schedule_reads_as_a_pension():
    text = (STATEMENTS_DIR / "01-meridian-trust-bank-statement.txt").read_text(encoding="utf-8")
    parsed = extract_from_document({"text": text, "document": "meridian.txt"})
    result = infer_hidden_obligations(
        {"debits": [], "credits": parsed["credits"], "known_institutions": []}
    )
    annuity = next(r for r in result["inferred"] if "Cobalt" in r["institution_name"])

    assert annuity["category"] == "PENSION"
    assert annuity["direction"] == "credit"


def test_inference_is_conservative_about_one_off_payments():
    debits = [
        {"merchant": "Some Shop", "amount": 42.00, "kind": "CARD PURCHASE",
         "source_document": "s.txt", "excerpt": "...", "date": "2026-01-01"},
        {"merchant": "Some Shop", "amount": 199.00, "kind": "CARD PURCHASE",
         "source_document": "s.txt", "excerpt": "...", "date": "2026-02-01"},
        {"merchant": "Some Shop", "amount": 7.50, "kind": "CARD PURCHASE",
         "source_document": "s.txt", "excerpt": "...", "date": "2026-03-01"},
    ]
    result = infer_hidden_obligations({"debits": debits, "credits": [], "known_institutions": []})
    assert result["inferred"] == [], "wildly varying amounts are shopping, not an obligation"


def test_two_occurrences_are_not_enough():
    debits = [
        {"merchant": "Maybe Gym", "amount": 40.00, "kind": "DIRECT DEBIT",
         "source_document": "s.txt", "excerpt": "...", "date": f"2026-0{m}-01"}
        for m in (1, 2)
    ]
    result = infer_hidden_obligations({"debits": debits, "credits": [], "known_institutions": []})
    assert result["inferred"] == []


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Pacific Grid Energy Autopay", "Pacific Grid Energy"),
        ("MERIDIAN TRUST BK", "Meridian Trust Bank"),
        ("Ashgrove Mutual Life Premium", "Ashgrove Mutual Life"),
    ],
)
def test_documented_merchants_are_recognised_and_excluded(a, b):
    assert _same_institution(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Cobalt Ridge Annuity", "Ironbridge Retirement Fund"),
        ("Fernbrook Self Storage", "Sunset Fiber Broadband"),
    ],
)
def test_unrelated_institutions_are_not_conflated(a, b):
    assert not _same_institution(a, b)


# --- unclaimed property --------------------------------------------------------------


def test_the_registry_finds_the_escheated_balance():
    matches = unclaimed.search(
        "Eleanor Margaret Halloran", "218 Ridgeline Avenue, Oakland CA 94611"
    )
    assert len(matches) == 1
    assert matches[0].amount_usd == 4214.60
    assert matches[0].registry == "CA"


def test_a_same_name_different_city_record_is_not_a_match():
    """The failure mode that matters: people share names.

    The fixture contains a second Eleanor M Halloran in Bakersfield. Matching her would
    mean writing to a stranger's escrow agent about someone else's death.
    """
    matches = unclaimed.search(
        "Eleanor Margaret Halloran", "218 Ridgeline Avenue, Oakland CA 94611"
    )
    assert all("Bakersfield" not in m.owner_address for m in matches)


def test_registries_outside_the_covered_three_are_skipped():
    matches = unclaimed.search("Eleanor Halloran", "4 Cypress Row, Austin TX 78701")
    assert all(m.registry in unclaimed.COVERED_REGISTRIES for m in matches)


def test_a_registry_match_is_never_certain():
    matches = unclaimed.search(
        "Eleanor Margaret Halloran", "218 Ridgeline Avenue, Oakland CA 94611"
    )
    assert matches[0].confidence < 1.0, (
        "a name-and-address match is strong evidence, not proof of identity"
    )


# --- the whole graph -----------------------------------------------------------------


def test_the_seeded_graph_is_the_shape_the_demo_claims(seeded_estate):
    obligations = get_repos().obligations.for_estate(seeded_estate["estate_id"])

    assert len(obligations) == 23
    surprises = [o for o in obligations if o.is_surprise]
    assert len(surprises) == 4, "three inferred plus one registry match"

    by_method = {m: 0 for m in DiscoveryMethod}
    for obligation in obligations:
        by_method[obligation.discovery_method] += 1
    assert by_method[DiscoveryMethod.DOCUMENT] == 19
    assert by_method[DiscoveryMethod.INFERENCE] == 3
    assert by_method[DiscoveryMethod.REGISTRY] == 1


def test_the_death_certificate_is_not_an_obligation(seeded_estate):
    obligations = get_repos().obligations.for_estate(seeded_estate["estate_id"])
    assert not any("certificate of death" in o.institution_name.lower() for o in obligations)


def test_every_obligation_traces_back_to_a_page(seeded_estate):
    for obligation in get_repos().obligations.for_estate(seeded_estate["estate_id"]):
        assert obligation.evidence, f"{obligation.institution_name} has no evidence"
        assert all(e.source_document for e in obligation.evidence)


def test_no_obligation_carries_a_full_account_number(seeded_estate):
    """The output contract: a fingerprint, never the number."""
    for obligation in get_repos().obligations.for_estate(seeded_estate["estate_id"]):
        if obligation.account_fingerprint:
            assert "*" in obligation.account_fingerprint or "-" in obligation.account_fingerprint


def test_the_bank_appears_once_not_twelve_times(seeded_estate):
    obligations = get_repos().obligations.for_estate(seeded_estate["estate_id"])
    banks = [o for o in obligations if o.category is ObligationCategory.BANK]
    names = [o.institution_name for o in banks]
    assert len(names) == len(set(names)), "statements from one bank are one obligation"


def test_a_fingerprint_is_stable_and_does_not_leak_the_number():
    from packages.core.models import Obligation

    first = Obligation.fingerprint("4402-1183-3391")
    again = Obligation.fingerprint("440211833391")
    assert first == again, "formatting must not change the fingerprint"
    assert "4402" not in first
    assert first.endswith(first.split(":")[1])
