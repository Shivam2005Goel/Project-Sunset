"""Configuration loading and model-failure handling.

Both of these exist because of the same class of problem: a misconfiguration that takes
ten minutes to diagnose because the software did not say what was wrong. A 404 from a
publisher model is a configuration fact, and a `.env` nobody reads is a documented step
that silently does nothing.
"""

from __future__ import annotations

import os

import pytest

from packages.core.config import load_dotenv


# --- .env ----------------------------------------------------------------------------


def test_dotenv_is_read(tmp_path, monkeypatch):
    """README section 3 says to create a .env. Something has to read it."""
    monkeypatch.delenv("MODEL_FAST", raising=False)
    env = tmp_path / ".env"
    env.write_text("MODEL_FAST=gemini-from-dotenv\n", encoding="utf-8")

    applied = load_dotenv(env)

    assert applied == {"MODEL_FAST": "gemini-from-dotenv"}
    assert os.environ["MODEL_FAST"] == "gemini-from-dotenv"
    monkeypatch.delenv("MODEL_FAST", raising=False)


def test_the_shell_beats_the_dotenv_file(tmp_path, monkeypatch):
    """Standard precedence, and the source of a genuinely confusing ten minutes.

    A stale `AFTERCARE_MODE=cloud` left in a shell silently overrides the `.env` you just
    edited. `python tasks.py doctor` prints both so the disagreement is visible.
    """
    monkeypatch.setenv("MODEL_FAST", "set-in-the-shell")
    env = tmp_path / ".env"
    env.write_text("MODEL_FAST=set-in-the-file\n", encoding="utf-8")

    applied = load_dotenv(env)

    assert applied == {}
    assert os.environ["MODEL_FAST"] == "set-in-the-shell"


def test_dotenv_parsing_handles_the_shapes_people_write(tmp_path, monkeypatch):
    for name in ("A_QUOTED", "B_EXPORTED", "C_SPACED", "D_HASH"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                'A_QUOTED="quoted value"',
                "export B_EXPORTED=exported",
                "  C_SPACED = spaced  ",
                "D_HASH=value",
                "NOT_AN_ASSIGNMENT",
            ]
        ),
        encoding="utf-8",
    )

    applied = load_dotenv(env)

    assert applied["A_QUOTED"] == "quoted value"
    assert applied["B_EXPORTED"] == "exported"
    assert applied["C_SPACED"] == "spaced"
    assert "NOT_AN_ASSIGNMENT" not in applied
    for name in applied:
        monkeypatch.delenv(name, raising=False)


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}


# --- model failures ------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "404 NOT_FOUND. Publisher model `gemini-x` was not found or your project does not have access to it",
        "403 PERMISSION_DENIED",
        "401 UNAUTHENTICATED",
        "API key not valid. Please pass a valid API key.",
        "Billing has not been enabled for this project",
    ],
)
def test_configuration_errors_are_recognised(message):
    from packages.core.llm import is_configuration_error

    assert is_configuration_error(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    ["503 Service Unavailable", "Deadline exceeded", "connection reset by peer"],
)
def test_transient_errors_are_not_treated_as_configuration(message):
    from packages.core.llm import is_configuration_error

    assert not is_configuration_error(RuntimeError(message))


def test_a_configuration_error_is_not_retried_and_explains_itself():
    """The failure the reader actually gets.

    Retrying a 404 three times and then printing a sixty-line SDK traceback tells you
    nothing you can act on. This asserts both halves: one attempt, and a message naming
    the fix.
    """
    from packages.core.llm import LLMClient, ModelUnavailable, Provider

    class NotFound(Provider):
        name = "vertex"

        def __init__(self):
            self.attempts = 0

        def generate(self, **kwargs):
            self.attempts += 1
            raise RuntimeError(
                "404 NOT_FOUND. Publisher model `gemini-x` was not found or your "
                "project does not have access to it."
            )

    provider = NotFound()
    with pytest.raises(ModelUnavailable) as excinfo:
        LLMClient(provider=provider).complete("guardrail.judge", prompt="x", retries=3)

    assert provider.attempts == 1, "a configuration error must not be retried"
    message = str(excinfo.value)
    assert "AFTERCARE_MODE=local" in message, "the fastest fix has to be in the message"
    assert "GEMINI_API_KEY" in message
    assert "tasks.py doctor" in message
    assert "404" in message, "the underlying cause is still reported"


def test_preflight_is_a_no_op_offline():
    from packages.core.llm import OFFLINE_MODEL, get_llm

    assert get_llm().preflight() == OFFLINE_MODEL


def test_preflight_surfaces_a_bad_configuration_before_real_work():
    from packages.core.llm import LLMClient, ModelUnavailable, Provider

    class NotFound(Provider):
        name = "vertex"

        def generate(self, **kwargs):
            raise RuntimeError("403 PERMISSION_DENIED")

    with pytest.raises(ModelUnavailable):
        LLMClient(provider=NotFound()).preflight()


def test_a_broken_cloud_model_never_silently_becomes_the_offline_planner():
    """The honesty rule, enforced.

    Falling back to rules-based reasoning in cloud mode without saying so would make the
    demo a lie: the submission claims Gemini is doing the reasoning. Local mode is that
    fallback, it is labelled, and it is one environment variable away.
    """
    from packages.core.llm import OFFLINE_MODEL, LLMClient, ModelUnavailable, Provider

    class NotFound(Provider):
        name = "vertex"

        def generate(self, **kwargs):
            raise RuntimeError("404 NOT_FOUND")

    client = LLMClient(provider=NotFound())
    with pytest.raises(ModelUnavailable):
        client.complete("guardrail.judge", prompt="x")
    assert client.provider_name != "offline"
    assert client.model_for("guardrail.judge") != OFFLINE_MODEL
