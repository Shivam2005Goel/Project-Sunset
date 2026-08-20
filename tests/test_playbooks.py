"""Playbooks, the registry, and the amendment loop.

The reuse argument is the strongest thing in this submission, so it needs to be more than
a claim: versions must be immutable, resolution must work off the messy names that appear
on real statements, and an amendment must be a diff a human can read before it becomes
the version every future estate inherits.
"""

from __future__ import annotations

import pytest

from packages.core.adapters.registry import (
    VersionedFileRegistry,
    VersionError,
    bump,
    diff_documents,
    parse_version,
)
from packages.core.config import get_settings
from packages.core.models import Channel, ObligationCategory
from packages.playbooks.publisher import load_catalog, publish_all, resolve
from packages.playbooks.schema import GENERIC_NAME, Playbook, generic_playbook


@pytest.fixture
def registry(tmp_path):
    from packages.core.adapters.registry import set_registry_adapter

    adapter = VersionedFileRegistry(tmp_path / "registry")
    set_registry_adapter(adapter)
    yield adapter
    set_registry_adapter(None)


# --- the catalog ---------------------------------------------------------------------


def test_the_catalog_covers_the_six_institutions_the_plan_calls_for():
    playbooks = load_catalog()
    categories = {p.category for p in playbooks}
    assert len(playbooks) == 6
    assert categories == {
        ObligationCategory.BANK,
        ObligationCategory.LIFE_INSURANCE,
        ObligationCategory.PENSION,
        ObligationCategory.TELECOM,
        ObligationCategory.UTILITY,
        ObligationCategory.BROKERAGE,
    }


def test_every_catalog_playbook_is_deep_not_a_stub():
    """Three deep beats six shallow, and six deep beats both. Hold the line on depth."""
    for playbook in load_catalog():
        assert playbook.required_documents, f"{playbook.name} lists no required documents"
        assert playbook.required_disclosures, f"{playbook.name} lists no disclosures"
        assert playbook.escalation_triggers, f"{playbook.name} has no escalation triggers"
        assert playbook.closure_signals, f"{playbook.name} cannot recognise being finished"
        assert playbook.submission_note, f"{playbook.name} carries no operational knowledge"
        assert playbook.source, f"{playbook.name} has no provenance"


def test_every_catalog_playbook_is_marked_fictional():
    assert all(p.fictional for p in load_catalog()), "invariant 6"


def test_playbook_names_are_registry_slugs():
    for playbook in load_catalog():
        assert playbook.name == playbook.name.lower()
        assert " " not in playbook.name


def test_a_channel_that_needs_an_address_must_have_one():
    with pytest.raises(ValueError, match="submission_address"):
        Playbook(
            name="no-address",
            institution_name="No Address Bank",
            category=ObligationCategory.BANK,
            submission_channel=Channel.EMAIL,
            submission_address="",
        )


def test_disclosures_must_name_fields_the_catalog_knows():
    with pytest.raises(ValueError, match="absent from the PII catalog"):
        Playbook(
            name="typo-bank",
            institution_name="Typo Bank",
            category=ObligationCategory.BANK,
            submission_channel=Channel.EMAIL,
            submission_address="x@example.invalid",
            required_disclosures=["decedent_naem"],
        )


# --- versioning ----------------------------------------------------------------------


def test_publishing_is_idempotent(registry):
    first = publish_all(registry)
    second = publish_all(registry)
    assert len(first) == len(second) == 7  # six institutions plus the generic template
    assert all("already published" in ref for ref in second)


def test_versions_are_immutable(registry):
    publish_all(registry)
    with pytest.raises(VersionError, match="immutable"):
        registry.publish("meridian-trust-bank", "1.0.0", {"institution_name": "Rewritten"})


def test_a_version_must_be_semver(registry):
    with pytest.raises(VersionError):
        registry.publish("x", "one-point-oh", {})


@pytest.mark.parametrize(
    ("current", "level", "expected"),
    [("1.0.0", "minor", "1.1.0"), ("1.4.2", "patch", "1.4.3"), ("2.9.9", "major", "3.0.0")],
)
def test_semver_bumps(current, level, expected):
    assert bump(current, level) == expected
    assert parse_version(expected)


def test_versions_sort_numerically_not_lexically(registry):
    for version in ("1.0.0", "1.2.0", "1.10.0", "1.9.0"):
        registry.publish("ordering", version, {"institution_name": "Ordering"})
    assert registry.list_versions("ordering") == ["1.0.0", "1.2.0", "1.9.0", "1.10.0"]
    assert registry.latest_version("ordering") == "1.10.0"


def test_fetch_by_name_and_version(registry):
    publish_all(registry)
    document = registry.fetch("cascadia-securities", "1.0.0")
    assert document["institution_name"] == "Cascadia Securities"
    assert document["published_at"], "a published version records when it was published"

    latest = registry.fetch("cascadia-securities", "latest")
    assert latest["version"] == "1.0.0"


def test_fetching_an_unknown_playbook_raises(registry):
    with pytest.raises(KeyError):
        registry.fetch("no-such-institution")


# --- diffing -------------------------------------------------------------------------


def test_diff_reports_added_and_removed_list_items():
    left = {"required_documents": ["a", "b"], "typical_sla_days": 10}
    right = {"required_documents": ["a", "b", "c"], "typical_sla_days": 14}
    changes = {c["field"]: c for c in diff_documents(left, right)}

    assert changes["required_documents"]["added"] == ["c"]
    assert changes["required_documents"]["removed"] == []
    assert changes["typical_sla_days"]["from"] == 10
    assert changes["typical_sla_days"]["to"] == 14


def test_diff_between_two_published_versions(registry):
    publish_all(registry)
    original = registry.fetch("northshore-wireless", "1.0.0")
    amended = {
        **original,
        "required_documents": [
            *original["required_documents"],
            "executor photo identification",
        ],
    }
    registry.publish("northshore-wireless", "1.1.0", amended)

    changes = registry.diff("northshore-wireless", "1.0.0", "1.1.0")
    added = next(c for c in changes if c["field"] == "required_documents")["added"]
    assert added == ["executor photo identification"]


# --- resolution ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement_name",
    ["MERIDIAN TRUST BK", "Meridian Trust", "Meridian Trust Bank NA", "Meridian Trust Bank"],
)
def test_resolution_works_off_the_names_that_appear_on_statements(registry, statement_name):
    publish_all(registry)
    playbook, ref, specific = resolve(statement_name, ObligationCategory.BANK, registry)
    assert specific is True
    assert ref == "meridian-trust-bank@1.0.0"
    assert playbook.department.startswith("Estate Services")


def test_an_unknown_institution_falls_back_and_says_so(registry):
    publish_all(registry)
    playbook, ref, specific = resolve(
        "Fernbrook Self Storage", ObligationCategory.OTHER, registry
    )
    assert specific is False
    assert ref.startswith(GENERIC_NAME)
    assert "generic closure template" in playbook.submission_note
    assert playbook.institution_name == "Fernbrook Self Storage"


def test_the_generic_template_is_honest_about_what_it_does_not_know():
    template = generic_playbook(ObligationCategory.OTHER, "Somebody")
    assert "one additional round trip" in template.submission_note
    assert "candidate for a real playbook" in template.notes


# --- the amendment loop --------------------------------------------------------------


def test_an_unanticipated_demand_proposes_a_new_version(registry, seeded_estate):
    from packages.playbooks.publisher import propose_amendment

    playbook = Playbook.from_document(registry.fetch("northshore-wireless"))
    proposal = propose_amendment(
        estate_id=seeded_estate["estate_id"],
        case_id="case_test",
        playbook=playbook,
        demanded_documents=["executor photo identification"],
        institution_name="Northshore Wireless",
        registry=registry,
    )
    assert proposal is not None
    assert proposal.from_version == "1.0.0"
    assert proposal.proposed_version == "1.1.0", "an added requirement is a minor bump"
    assert proposal.add_required_documents == ["executor photo identification"]
    assert proposal.rationale
    assert proposal.diff, "the executor approves a diff, not a version number"
    assert proposal.status == "PROPOSED", "nothing is published before a human sees it"


def test_a_demand_the_playbook_already_knows_proposes_nothing(registry, seeded_estate):
    from packages.playbooks.publisher import propose_amendment

    playbook = Playbook.from_document(registry.fetch("meridian-trust-bank"))
    proposal = propose_amendment(
        estate_id=seeded_estate["estate_id"],
        case_id="case_test",
        playbook=playbook,
        demanded_documents=["certified copy of the death certificate"],
        institution_name="Meridian Trust Bank",
        registry=registry,
    )
    assert proposal is None, "a registry full of no-op versions is noise"


def test_a_gap_in_the_generic_template_is_not_an_amendment(registry, seeded_estate):
    from packages.playbooks.publisher import propose_amendment

    template = generic_playbook(ObligationCategory.OTHER, "Fernbrook Self Storage")
    proposal = propose_amendment(
        estate_id=seeded_estate["estate_id"],
        case_id="case_test",
        playbook=template,
        demanded_documents=["something unusual"],
        institution_name="Fernbrook Self Storage",
        registry=registry,
    )
    assert proposal is None, (
        "the generic template not knowing something says nothing about the institution - "
        "it says this institution deserves its own playbook"
    )


def test_applying_an_amendment_publishes_the_new_version(registry, seeded_estate):
    from packages.playbooks.publisher import apply_amendment, propose_amendment

    playbook = Playbook.from_document(registry.fetch("northshore-wireless"))
    proposal = propose_amendment(
        estate_id=seeded_estate["estate_id"],
        case_id="case_test",
        playbook=playbook,
        demanded_documents=["executor photo identification"],
        institution_name="Northshore Wireless",
        registry=registry,
    )
    ref = apply_amendment(proposal.id, registry)

    assert ref == "northshore-wireless@1.1.0"
    assert registry.list_versions("northshore-wireless") == ["1.0.0", "1.1.0"]

    updated = Playbook.from_document(registry.fetch("northshore-wireless", "latest"))
    assert "executor photo identification" in updated.required_documents
    assert "1.0.0" in registry.fetch("northshore-wireless", "1.0.0")["version"], (
        "publishing a new version must not rewrite the old one"
    )


def test_the_registry_catalog_view_is_what_the_dashboard_needs(registry):
    publish_all(registry)
    catalog = registry.catalog()
    assert len(catalog) == 7
    entry = next(e for e in catalog if e["name"] == "ashgrove-mutual-life")
    assert entry["display_name"] == "Ashgrove Mutual Life"
    assert entry["category"] == "LIFE_INSURANCE"
    assert entry["latest"] == "1.0.0"


def test_local_mode_never_reaches_for_the_managed_registry():
    assert get_settings().effective_adapter("registry") == "gcs_versioned"
