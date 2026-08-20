"""The fiduciary record.

An executor can be sued. The audit log is the evidence, so "append-only by convention"
is not enough - the chain has to be checkable by someone who does not trust the person
handing it to them. These tests are the ones that make the phrase "court-defensible"
mean something.
"""

from __future__ import annotations

import json

import pytest

from packages.core.audit.export import export_estate_record, render_html
from packages.core.audit.sink import GENESIS, AuditLog, JsonlAuditSink
from packages.core.models import Decedent, Estate, Executor


@pytest.fixture
def log(tmp_path):
    return AuditLog(JsonlAuditSink(tmp_path / "audit.jsonl"))


@pytest.fixture
def estate():
    return Estate(
        decedent=Decedent(
            full_name="Eleanor Margaret Halloran",
            date_of_birth="1948-03-11",
            date_of_death="2026-06-14",
            last_address="218 Ridgeline Avenue, Oakland CA 94611",
        ),
        executor=Executor(full_name="Daniel R. Halloran", email="d@example.invalid"),
    )


def _write(log: AuditLog, count: int, estate_id: str = "est_test") -> None:
    for index in range(count):
        log.record(
            estate_id=estate_id,
            actor="worker",
            action=f"test.action.{index}",
            reasoning=f"Step {index} taken because the playbook said so.",
            payload={"index": index},
        )


# --- the chain -----------------------------------------------------------------------


def test_the_first_record_chains_from_genesis(log, frozen_clock):
    record = log.record(estate_id="est_test", actor="system", action="a", reasoning="r")
    assert record.seq == 1
    assert record.prev_digest == GENESIS
    assert record.digest == record.compute_digest()


def test_each_record_carries_its_predecessors_digest(log, frozen_clock):
    _write(log, 5)
    records = log.all()
    assert [r.seq for r in records] == [1, 2, 3, 4, 5]
    for previous, current in zip(records, records[1:]):
        assert current.prev_digest == previous.digest


def test_a_clean_chain_verifies(log, frozen_clock):
    _write(log, 10)
    ok, broken_at = log.verify()
    assert ok is True
    assert broken_at is None


def test_editing_a_record_in_the_middle_is_detected(log, frozen_clock, tmp_path):
    """The property that matters.

    Someone with write access to the log edits record 5 to remove an action they regret.
    Every subsequent digest no longer follows, and verify() names the record where the
    story stops adding up.
    """
    _write(log, 10)
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()

    tampered = json.loads(lines[4])
    tampered["reasoning"] = "Nothing to see here."
    lines[4] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reopened = AuditLog(JsonlAuditSink(path))
    ok, broken_at = reopened.verify()
    assert ok is False
    assert broken_at == tampered["id"]


def test_deleting_a_record_is_detected(log, frozen_clock, tmp_path):
    _write(log, 6)
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, broken_at = AuditLog(JsonlAuditSink(path)).verify()
    assert ok is False
    assert broken_at is not None


def test_reopening_a_log_continues_the_chain(log, frozen_clock, tmp_path):
    _write(log, 3)
    reopened = AuditLog(JsonlAuditSink(tmp_path / "audit.jsonl"))
    record = reopened.record(estate_id="est_test", actor="system", action="later", reasoning="r")

    assert record.seq == 4
    assert reopened.verify()[0] is True


def test_records_are_scoped_by_estate_and_case(log, frozen_clock):
    _write(log, 2, estate_id="est_a")
    _write(log, 3, estate_id="est_b")
    log.record(
        estate_id="est_a", case_id="case_1", actor="worker", action="x", reasoning="r"
    )

    assert len(log.for_estate("est_a")) == 3
    assert len(log.for_estate("est_b")) == 3
    assert len(log.for_case("case_1")) == 1


# --- what a record has to contain ----------------------------------------------------


def test_every_record_carries_a_reasoning_chain(seeded_estate):
    from packages.core.audit.sink import get_audit_log

    records = get_audit_log().for_estate(seeded_estate["estate_id"])
    assert records, "seeding must produce an audit trail"
    for record in records:
        assert record.reasoning.strip(), f"{record.action} recorded no reasoning"
        assert record.actor.strip()


def test_the_seeded_run_records_discovery_and_the_fleet(seeded_estate):
    from packages.core.audit.sink import get_audit_log

    actions = {r.action for r in get_audit_log().for_estate(seeded_estate["estate_id"])}
    assert "discovery.started" in actions
    assert "discovery.completed" in actions
    assert "fleet.planned" in actions
    assert "case.opened" in actions
    assert any(a.startswith("fsm.") for a in actions)


def test_nothing_was_sent_during_seeding(seeded_estate):
    """Seeding produces a full estate and zero outbound communications."""
    from packages.core.audit.sink import get_audit_log

    actions = [r.action for r in get_audit_log().for_estate(seeded_estate["estate_id"])]
    assert "outbound.sent" not in actions


def test_the_inference_reasoning_is_readable_by_a_human(seeded_estate):
    from packages.core.audit.sink import get_audit_log

    inferred = [
        r
        for r in get_audit_log().for_estate(seeded_estate["estate_id"])
        if r.action == "obligation.inferred"
    ]
    assert len(inferred) == 3
    for record in inferred:
        assert "debits" in record.reasoning or "credits" in record.reasoning
        assert "did not list" in record.reasoning


# --- export --------------------------------------------------------------------------


def test_the_export_states_whether_the_chain_verified(log, estate, frozen_clock):
    _write(log, 4, estate_id=estate.id)
    html = render_html(estate, log.for_estate(estate.id), True, None)

    assert "VERIFIED" in html
    assert estate.decedent.full_name in html
    assert estate.executor.full_name in html
    assert "not legal advice" in html
    assert "fictional" in html.lower()


def test_a_broken_chain_is_stated_on_the_document(log, estate, frozen_clock):
    _write(log, 2, estate_id=estate.id)
    html = render_html(estate, log.for_estate(estate.id), False, "aud_broken")
    assert "BROKEN" in html
    assert "aud_broken" in html


def test_the_export_writes_a_file_that_opens(log, estate, frozen_clock, tmp_path):
    _write(log, 3, estate_id=estate.id)
    produced = export_estate_record(estate, log=log, out_dir=tmp_path)

    assert "html" in produced
    assert produced["html"].exists()
    content = produced["html"].read_text(encoding="utf-8")
    assert content.startswith("<!doctype html>")
    assert "Estate Administration Record" in content


def test_the_export_escapes_content_rather_than_rendering_it(log, estate, frozen_clock):
    """Inbound text reaches the audit log. The export must not become an XSS vector."""
    log.record(
        estate_id=estate.id,
        actor="guardrail",
        action="inbound.blocked",
        reasoning="Blocked a letter containing <script>alert('x')</script>",
    )
    html = render_html(estate, log.for_estate(estate.id), True, None)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
