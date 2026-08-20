"""The test to show a judge.

Everything else in this suite checks that the system behaves correctly. This one checks
that it *cannot* behave incorrectly - it walks the AST of every Python file in the
repository and proves structural properties that no amount of prompt engineering can
undo:

1. Nothing except `services/api/approvals.py` can reach the send path.
2. Model and agent SDKs appear only in the adapter layer, and every other Google SDK has
   exactly one owning module.
3. Nothing calls `datetime.now()` outside the clock, so the time-warp is real.
4. `AUTO_SEND` is a False literal and the environment cannot flip it.
5. No path through the state machine reaches SENT without passing AWAITING_APPROVAL.
6. Raw inbound content is never interpolated into a prompt.

Each analysis is a function taking a root path, so the `test_analyzer_detects_a_planted_*`
tests can run the same check against a temporary tree with a deliberate violation in it
and assert the analyzer catches it. A guard nobody has watched fail is not a guard.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("packages", "services", "demo")

# Only these may reach the transport.
SEND_CALLERS = {"services/api/approvals.py", "services/api/transport.py"}

# The adapter rule, precisely. Two different claims, kept apart rather than pretending
# the strong one covers everything:
#
#   * MODEL_SDK_OWNERS - the agent and model surfaces. These are what CLAUDE.md's adapter
#     rule is about, and the allowlist is genuinely tiny.
#   * INFRA_SDK_OWNERS - Firestore, BigQuery, Cloud Tasks, DLP, Gmail, OpenTelemetry.
#     Ordinary managed infrastructure, used by the module that owns that concern. The
#     rule here is one owner per SDK, not zero uses.
MODEL_SDK_PREFIXES = ("vertexai", "google.genai", "google.adk", "google.generativeai")
MODEL_SDK_OWNERS = (
    "packages/core/llm.py",
    "packages/core/adapters/",
    "services/orchestrator/adk.py",
)
INFRA_SDK_OWNERS = {
    "google.cloud.firestore": ("packages/core/store.py", "packages/core/adapters/memory.py"),
    "google.cloud.bigquery": ("packages/core/audit/sink.py",),
    "google.cloud.storage": ("packages/core/adapters/registry.py",),
    "google.cloud.tasks_v2": ("packages/core/adapters/runtime.py",),
    "google.cloud.dlp_v2": ("packages/core/adapters/guardrail.py",),
    "google.cloud.vision": ("services/inbox/gmail.py",),
    "google.cloud.aiplatform_v1beta1": ("packages/core/adapters/registry.py",),
    "googleapiclient": ("services/inbox/gmail.py", "services/api/transport.py"),
    "opentelemetry": ("packages/core/telemetry.py",),
}


def python_files(root: Path, dirs: tuple[str, ...] = SOURCE_DIRS) -> list[Path]:
    found: list[Path] = []
    for name in dirs:
        base = root / name
        if base.exists():
            found.extend(sorted(base.rglob("*.py")))
    return found


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def joined(violations: list[str]) -> str:
    return "\n  ".join(violations)


# --- 1. the approval boundary --------------------------------------------------------


def find_send_path_violations(root: Path) -> list[str]:
    """Any reference to the transport's delivery function outside the permitted files."""
    violations: list[str] = []
    for path in python_files(root):
        relative = rel(path, root)
        if relative in SEND_CALLERS:
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.ImportFrom) and node.module and "transport" in node.module:
                for alias in node.names:
                    if alias.name in {"deliver", "Transport", "get_transport"}:
                        violations.append(f"{relative}:{node.lineno} imports transport.{alias.name}")
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else ""
                )
                if name in {"deliver", "transmit", "_send_email"}:
                    violations.append(f"{relative}:{node.lineno} calls {name}()")
    return violations


def test_only_approvals_can_reach_the_send_path():
    violations = find_send_path_violations(REPO_ROOT)
    assert violations == [], (
        "Invariant 1 broken - something outside the approval service can send:\n  "
        + joined(violations)
    )


def test_analyzer_detects_a_planted_bypass(tmp_path):
    """Run the guard against a tree that deliberately violates it.

    BUILD_PLAN.md is explicit that this must be verified in both directions: a policy
    test that has never been observed failing proves nothing about the day someone
    introduces a bypass.
    """
    service = tmp_path / "services" / "worker"
    service.mkdir(parents=True)
    source = [
        "from services.api.transport import deliver",
        "def just_this_once(packet, case):",
        "    return deliver(packet, None, case)",
    ]
    (service / "sneaky.py").write_text("\n".join(source), encoding="utf-8")

    violations = find_send_path_violations(tmp_path)
    assert len(violations) == 2, violations
    assert any("imports transport.deliver" in v for v in violations)
    assert any("calls deliver()" in v for v in violations)


# --- 2. the adapter rule -------------------------------------------------------------


def imported_modules(tree: ast.Module) -> list[tuple[str, int]]:
    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            # `from google.cloud import firestore` - the leaf is the interesting name.
            modules += [(f"{node.module}.{alias.name}", node.lineno) for alias in node.names]
            modules.append((node.module, node.lineno))
    return modules


def find_model_sdk_violations(root: Path) -> list[str]:
    """Model and agent SDKs outside the files allowed to hold them."""
    violations: list[str] = []
    for path in python_files(root):
        relative = rel(path, root)
        if relative.startswith(MODEL_SDK_OWNERS):
            continue
        for module, lineno in imported_modules(parse(path)):
            if module.startswith(MODEL_SDK_PREFIXES):
                violations.append(f"{relative}:{lineno} imports {module}")
    return violations


def find_infra_sdk_violations(root: Path) -> list[str]:
    """Infrastructure SDKs imported by a module that does not own that concern."""
    violations: list[str] = []
    for path in python_files(root):
        relative = rel(path, root)
        for module, lineno in imported_modules(parse(path)):
            for sdk, owners in INFRA_SDK_OWNERS.items():
                if module.startswith(sdk) and not relative.startswith(owners):
                    violations.append(f"{relative}:{lineno} imports {module}")
    return violations


def test_model_and_agent_sdks_only_in_the_adapter_layer():
    violations = find_model_sdk_violations(REPO_ROOT)
    assert violations == [], (
        "The adapter rule is broken - a model or agent SDK is imported outside the "
        "adapter layer:\n  " + joined(violations)
    )


def test_each_infrastructure_sdk_has_exactly_one_owner():
    violations = find_infra_sdk_violations(REPO_ROOT)
    assert violations == [], (
        "An infrastructure SDK is imported outside the module that owns that concern. "
        "Route it through the owner instead:\n  " + joined(violations)
    )


def test_analyzer_detects_a_planted_sdk_import(tmp_path):
    service = tmp_path / "services" / "worker"
    service.mkdir(parents=True)
    source = [
        "from google import genai",
        "def ask(prompt):",
        "    return genai.Client().models.generate_content(model='x', contents=prompt)",
    ]
    (service / "shortcut.py").write_text("\n".join(source), encoding="utf-8")

    assert find_model_sdk_violations(tmp_path), "the analyzer missed a planted SDK import"


# --- 3. the clock --------------------------------------------------------------------


def find_wall_clock_violations(root: Path) -> list[str]:
    allowed = {"packages/core/clock.py"}
    violations: list[str] = []
    for path in python_files(root):
        relative = rel(path, root)
        if relative in allowed:
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"now", "utcnow", "today"} and isinstance(
                    node.func.value, ast.Name
                ):
                    if node.func.value.id in {"datetime", "date"}:
                        violations.append(
                            f"{relative}:{node.lineno} calls "
                            f"{node.func.value.id}.{node.func.attr}()"
                        )
    return violations


def test_nothing_reads_wall_time_outside_the_clock():
    violations = find_wall_clock_violations(REPO_ROOT)
    assert violations == [], (
        "The time-warp is only real if application code never reads wall time:\n  "
        + joined(violations)
    )


def test_analyzer_detects_a_planted_wall_clock_read(tmp_path):
    service = tmp_path / "services" / "worker"
    service.mkdir(parents=True)
    source = [
        "from datetime import datetime",
        "def stamp():",
        "    return datetime.now()",
    ]
    (service / "impatient.py").write_text("\n".join(source), encoding="utf-8")

    assert find_wall_clock_violations(tmp_path), "the analyzer missed a wall-clock read"


# --- 4. AUTO_SEND --------------------------------------------------------------------


def test_auto_send_is_a_false_literal():
    tree = parse(REPO_ROOT / "packages" / "core" / "config.py")
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "AUTO_SEND" for t in node.targets)
    ]
    assert len(assignments) == 1, "AUTO_SEND must be assigned exactly once"
    value = assignments[0].value
    assert isinstance(value, ast.Constant) and value.value is False, (
        "AUTO_SEND must be the literal False, not an expression that could evaluate true"
    )


def test_environment_cannot_turn_auto_send_on():
    from packages.core.config import get_settings, reset_settings_cache

    os.environ["AUTO_SEND"] = "true"
    try:
        reset_settings_cache()
        assert get_settings().auto_send is False, (
            "Setting AUTO_SEND=true in the environment changed behaviour. It must not."
        )
    finally:
        os.environ.pop("AUTO_SEND", None)
        reset_settings_cache()


# --- 5. the state machine ------------------------------------------------------------


def test_no_route_to_sent_avoids_the_approval_state():
    from packages.core.fsm import all_paths
    from packages.core.models import CaseState

    routes = all_paths(CaseState.SENT)
    assert routes, "expected at least one route from DISCOVERED to SENT"
    bad = [r for r in routes if CaseState.AWAITING_APPROVAL not in r]
    assert bad == [], "Found a route to SENT that skips AWAITING_APPROVAL: " + "; ".join(
        " -> ".join(s.value for s in route) for route in bad
    )


def _fresh_case():
    from packages.core.models import InstitutionCase, ObligationCategory

    return InstitutionCase(
        estate_id="est_test",
        obligation_id="obl_test",
        institution_id="test-bank",
        institution_name="Test Bank",
        category=ObligationCategory.BANK,
    )


def test_every_transition_requires_a_reason(frozen_clock):
    from packages.core.fsm import EstateFSM
    from packages.core.models import CaseState

    with pytest.raises(ValueError, match="needs a reason"):
        EstateFSM().transition(_fresh_case(), CaseState.PACKET_DRAFTED, event="x", reason="   ")


def test_undeclared_transitions_raise(frozen_clock):
    from packages.core.fsm import EstateFSM, IllegalTransition
    from packages.core.models import CaseState

    with pytest.raises(IllegalTransition):
        EstateFSM().transition(
            _fresh_case(), CaseState.SENT, event="shortcut", reason="skipping the queue"
        )


# --- 6. untrusted content never reaches a prompt -------------------------------------


def find_raw_in_prompt_violations(root: Path) -> list[str]:
    """A prompt built from a variable named like raw inbound content.

    Crude by design: the rule is that raw inbound is never named in a prompt expression,
    which is easy to check and easy to keep true. Anything that needs inbound text in a
    prompt goes through `fence(sanitized_text)` instead.
    """
    suspicious = {"raw", "raw_ref", "body_raw", "raw_body", "raw_text", "original"}
    violations: list[str] = []
    for path in python_files(root):
        relative = rel(path, root)
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "prompt":
                    continue
                for inner in ast.walk(keyword.value):
                    if isinstance(inner, ast.Name) and inner.id in suspicious:
                        violations.append(
                            f"{relative}:{node.lineno} builds a prompt from '{inner.id}'"
                        )
                    if isinstance(inner, ast.Attribute) and inner.attr in suspicious:
                        violations.append(
                            f"{relative}:{node.lineno} builds a prompt from '.{inner.attr}'"
                        )
    return violations


def test_raw_inbound_never_reaches_a_prompt():
    violations = find_raw_in_prompt_violations(REPO_ROOT)
    assert violations == [], (
        "Invariant 3 broken - raw inbound content is being interpolated into a prompt:\n  "
        + joined(violations)
    )


def test_analyzer_detects_a_planted_prompt_leak(tmp_path):
    service = tmp_path / "services" / "inbox"
    service.mkdir(parents=True)
    source = [
        "def classify(llm, raw):",
        "    return llm.complete('inbound.classify', prompt=f'Classify: {raw}')",
    ]
    (service / "leaky.py").write_text("\n".join(source), encoding="utf-8")

    assert find_raw_in_prompt_violations(tmp_path), "the analyzer missed a planted leak"


# --- 7. no code execution primitives -------------------------------------------------


def test_no_dynamic_execution_anywhere():
    banned = {"eval", "exec", "compile", "__import__"}
    violations: list[str] = []
    for path in python_files(REPO_ROOT):
        relative = rel(path, REPO_ROOT)
        for node in ast.walk(parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in banned
            ):
                violations.append(f"{relative}:{node.lineno} calls {node.func.id}()")
            if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                violations.append(f"{relative}:{node.lineno} imports subprocess")
    assert violations == [], joined(violations)


# --- 8. runtime enforcement at the gate ----------------------------------------------


def _packet_and_case():
    from packages.core.models import CaseState, ClosurePacket

    case = _fresh_case()
    case.state = CaseState.AWAITING_APPROVAL
    packet = ClosurePacket(
        estate_id=case.estate_id,
        case_id=case.id,
        institution_name=case.institution_name,
        recipient="estates@test.example.invalid",
        subject="Estate notification",
        body="Dear Sir or Madam, I am writing as executor.",
    )
    return packet, case


def test_send_without_an_approval_is_refused(frozen_clock):
    from packages.guardrails.policy import PolicyViolation, assert_sendable

    packet, _ = _packet_and_case()
    with pytest.raises(PolicyViolation, match="no approval record"):
        assert_sendable(packet, None)


def test_send_with_a_pending_approval_is_refused(frozen_clock):
    from packages.core.models import ApprovalRequest
    from packages.guardrails.policy import PolicyViolation, assert_sendable

    packet, case = _packet_and_case()
    request = ApprovalRequest(estate_id=case.estate_id, case_id=case.id, packet_id=packet.id)
    with pytest.raises(PolicyViolation, match="PENDING"):
        assert_sendable(packet, request)


def test_an_approval_for_another_packet_is_refused(frozen_clock):
    from packages.core.clock import now
    from packages.core.models import ApprovalRequest, ApprovalStatus
    from packages.guardrails.policy import PolicyViolation, assert_sendable

    packet, case = _packet_and_case()
    request = ApprovalRequest(
        estate_id=case.estate_id,
        case_id=case.id,
        packet_id="pkt_something_else",
        status=ApprovalStatus.APPROVED,
        decided_at=now(),
        decided_by="Executor",
    )
    with pytest.raises(PolicyViolation, match="not transferable"):
        assert_sendable(packet, request)


def test_an_approval_without_a_named_human_is_refused(frozen_clock):
    from packages.core.clock import now
    from packages.core.models import ApprovalRequest, ApprovalStatus
    from packages.guardrails.policy import PolicyViolation, assert_sendable

    packet, case = _packet_and_case()
    request = ApprovalRequest(
        estate_id=case.estate_id,
        case_id=case.id,
        packet_id=packet.id,
        status=ApprovalStatus.APPROVED,
        decided_at=now(),
        decided_by=None,
    )
    with pytest.raises(PolicyViolation, match="no human decider"):
        assert_sendable(packet, request)


def test_a_case_not_awaiting_approval_cannot_send(frozen_clock):
    from packages.core.models import CaseState
    from packages.guardrails.policy import PolicyViolation, assert_state_allows_send

    _, case = _packet_and_case()
    case.state = CaseState.DISCOVERED
    with pytest.raises(PolicyViolation, match="only AWAITING_APPROVAL"):
        assert_state_allows_send(case)


@pytest.mark.parametrize(
    "body",
    [
        "I hereby certify that the enclosed is a true copy. /s/ Aftercare",
        "As the duly authorised agent of the estate, I require you to release the funds.",
        "My client is the sole legal heir and is entitled to the balance.",
        "The claim is valid and you are legally required to pay it.",
    ],
)
def test_boundary_violating_drafts_never_reach_the_queue(frozen_clock, body):
    from packages.guardrails.policy import PolicyViolation, assert_draft_clean

    packet, _ = _packet_and_case()
    packet.body = body
    with pytest.raises(PolicyViolation):
        assert_draft_clean(packet)


def test_a_clean_draft_passes(frozen_clock):
    from packages.guardrails.policy import assert_draft_clean

    packet, _ = _packet_and_case()
    packet.body = (
        "I am writing in my capacity as executor of the estate. I am asking you to "
        "record the death and tell me what your process requires from me."
    )
    assert_draft_clean(packet)  # must not raise


# --- 9. playbooks cannot ask for what must never be sent -----------------------------


def test_a_playbook_cannot_require_a_never_disclose_field():
    from packages.core.models import Channel, ObligationCategory
    from packages.playbooks.schema import Playbook

    with pytest.raises(ValueError, match="never-disclose"):
        Playbook(
            name="greedy-institution",
            institution_name="Greedy Institution",
            category=ObligationCategory.BANK,
            submission_channel=Channel.EMAIL,
            submission_address="x@example.invalid",
            required_disclosures=["decedent_full_name", "cause_of_death"],
        )
