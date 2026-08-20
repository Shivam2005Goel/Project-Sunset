"""Model routing, and the honesty rules around the offline planner.

The offline planner is the thing most likely to be mistaken for a shortcut, so it gets
its own tests. Two properties matter:

* it supports **every** task the cloud providers do, so local mode never silently loses a
  capability; and
* everything it produces is labelled as its own work, never as model output.
"""

from __future__ import annotations

import pytest

from packages.core import offline
from packages.core.llm import (
    DEEP_TASKS,
    OFFLINE_MODEL,
    LLMClient,
    OfflineProvider,
    _loose_json,
    approx_tokens,
    get_llm,
)


def test_local_mode_uses_the_offline_planner():
    assert get_llm().provider_name == "offline"


def test_every_task_the_codebase_calls_has_an_offline_handler():
    """Adding a task means adding a handler. Otherwise local mode breaks on Day 9."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    called: set[str] = set()
    for path in [*(root / "packages").rglob("*.py"), *(root / "services").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "complete"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                called.add(node.args[0].value)

    assert called, "expected to find llm.complete() call sites"
    missing = called - set(offline.HANDLERS)
    assert missing == set(), f"tasks with no offline handler: {sorted(missing)}"


def test_an_unknown_task_fails_loudly_rather_than_returning_nothing():
    client = LLMClient(provider=OfflineProvider())
    with pytest.raises(LookupError, match="No offline handler"):
        client.complete("nonexistent.task", prompt="x")


def test_offline_output_is_labelled_as_its_own_work():
    """The demo may run without a model. It may never pretend to have used one."""
    response = get_llm().complete(
        "inbound.classify",
        prompt="classify",
        inputs={"sanitized_text": "The account has been closed.", "subject": ""},
    )
    assert response.model == OFFLINE_MODEL
    assert response.is_offline
    assert "offline" in response.model


def test_drafted_packets_record_which_model_wrote_them(seeded_estate):
    from packages.core.repos import get_repos

    packets = get_repos().packets.query(estate_id=seeded_estate["estate_id"])
    assert packets
    assert all(p.model_used == OFFLINE_MODEL for p in packets), (
        "every artifact must carry the provenance of what produced it"
    )


def test_token_accounting_runs_on_every_call():
    client = LLMClient(provider=OfflineProvider())
    client.complete("guardrail.judge", prompt="a" * 400, inputs={"text": "hello"})
    client.complete("guardrail.judge", prompt="b" * 400, inputs={"text": "hello"})

    assert client.calls == 2
    assert client.usage.prompt_tokens >= 200
    assert client.usage.total > client.usage.prompt_tokens


def test_calls_emit_a_span():
    from packages.core.telemetry import recorder

    recorder().clear()
    get_llm().complete("guardrail.judge", prompt="x", inputs={"text": "hello"})
    spans = recorder().by_name("llm.complete")

    assert len(spans) == 1
    assert spans[0].attributes["task"] == "guardrail.judge"
    assert spans[0].attributes["model"] == OFFLINE_MODEL
    assert "tokens" in spans[0].attributes


def test_only_closure_drafting_escalates_to_the_expensive_model():
    """Flash-first is the cost story. Keep the exception list short and deliberate."""
    assert DEEP_TASKS == {"packet.draft"}


def test_model_selection_follows_the_task(monkeypatch):
    from packages.core.config import get_settings
    from packages.core.llm import Provider

    class Fake(Provider):
        name = "fake"

        def generate(self, *, model, prompt, task, inputs, images=None, json_mode=True):
            return "{}", {}

    settings = get_settings()
    client = LLMClient(settings=settings, provider=Fake())
    assert client.model_for("inbound.classify") == settings.model_fast
    assert client.model_for("packet.draft") == settings.model_deep
    assert client.model_for("packet.draft", deep=False) == settings.model_fast


def test_retries_then_surfaces_the_error():
    from packages.core.llm import Provider

    class Flaky(Provider):
        name = "flaky"

        def __init__(self):
            self.attempts = 0

        def generate(self, **kwargs):
            self.attempts += 1
            raise RuntimeError("upstream unavailable")

    provider = Flaky()
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        LLMClient(provider=provider).complete("x", prompt="y", retries=2)
    assert provider.attempts == 3, "two retries after the first attempt"


def test_a_transient_failure_recovers():
    from packages.core.llm import Provider

    class Flaky(Provider):
        name = "flaky"

        def __init__(self):
            self.attempts = 0

        def generate(self, **kwargs):
            self.attempts += 1
            if self.attempts < 2:
                raise RuntimeError("hiccup")
            return '{"ok": true}', {"ok": True}

    response = LLMClient(provider=Flaky()).complete("x", prompt="y")
    assert response.data == {"ok": True}


# --- parsing what models actually return ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"label": "COMPLETION"}', {"label": "COMPLETION"}),
        ('```json\n{"label": "REJECTION"}\n```', {"label": "REJECTION"}),
        ('Sure! Here you go:\n{"label": "UNKNOWN"}\nHope that helps.', {"label": "UNKNOWN"}),
    ],
)
def test_fenced_and_chatty_json_is_recovered(raw, expected):
    assert _loose_json(raw) == expected


def test_unparseable_output_is_preserved_rather_than_dropped():
    assert _loose_json("not json at all")["raw"] == "not json at all"


def test_token_estimate_is_monotonic():
    assert approx_tokens("short") < approx_tokens("a much longer string " * 20)


# --- the offline handlers themselves -------------------------------------------------


def test_classification_prefers_the_action_the_estate_has_to_take():
    """A letter that both acknowledges and asks for documents is a document request."""
    result = offline.classify_correspondence(
        {
            "sanitized_text": (
                "We have received your letter. Before we can proceed we require the "
                "completed Form DA-2."
            ),
            "subject": "",
        }
    )
    assert result["label"] == "DOCUMENT_REQUEST"


def test_classification_extracts_the_documents_and_the_deadline():
    result = offline.classify_correspondence(
        {
            "sanitized_text": (
                "We require a certified copy of the death certificate and the executor's "
                "photo identification within 14 business days."
            ),
            "subject": "",
        }
    )
    assert "certified copy of the death certificate" in result["requested_documents"]
    assert "executor photo identification" in result["requested_documents"]
    assert result["deadline"] == "14 business days"


def test_an_unclassifiable_letter_says_so_rather_than_guessing():
    result = offline.classify_correspondence(
        {"sanitized_text": "The weather has been unseasonable.", "subject": ""}
    )
    assert result["label"] == "UNKNOWN"
    assert result["confidence"] < 0.5


def test_an_escalation_brief_recommends_but_does_not_decide():
    result = offline.escalation_brief(
        {
            "institution_name": "Ashgrove Mutual Life",
            "trigger": "the beneficiary designation is contested",
            "category": "LIFE_INSURANCE",
        }
    )
    assert result["requires_human"] is True
    assert "decision is yours" in result["brief"]
    assert "Nothing has been sent" in result["brief"]
    assert len(result["options"]) >= 2


def test_a_draft_never_signs_and_never_asserts_authority():
    result = offline.draft_packet(
        {
            "institution_name": "Meridian Trust Bank",
            "playbook": {"name": "meridian-trust-bank", "version": "1.0.0"},
            "decedent": {"full_name": "E M Halloran", "date_of_death": "2026-06-14"},
            "executor": {"full_name": "D R Halloran", "email": "d@example.invalid"},
            "disclosures": [
                {"field": "decedent_full_name", "disclosed": True, "value": "E M Halloran"}
            ],
        }
    )
    body = result["body"].lower()
    assert "/s/" not in body
    assert "power of attorney" not in body
    assert "i hereby certify" not in body
    assert "in my capacity as executor" in body


def test_an_amendment_that_adds_a_requirement_is_a_minor_bump():
    result = offline.propose_amendment(
        {
            "current_version": "1.0.0",
            "required_documents": ["a"],
            "demanded_documents": ["a", "b"],
            "institution_name": "Somebody",
        }
    )
    assert result["proposed_version"] == "1.1.0"
    assert result["add_required_documents"] == ["b"]
    assert result["estimated_round_trips_saved"] == 1
