"""Guardrails: inbound injection screening, outbound PII minimization, policy boundaries.

Inbound and outbound are deliberately separate modules with separate tests. They fail in
opposite directions - inbound errs toward blocking, outbound errs toward withholding -
and merging them would blur that.
"""

from packages.guardrails.inbound import fence, sanitize, screen, screen_message, split_layers
from packages.guardrails.pii import (
    FIELD_CATALOG,
    NEVER_DISCLOSE,
    DisclosureRefused,
    detect,
    diff_view,
    minimize,
    scrub,
    summarize,
)
from packages.guardrails.policy import (
    PolicyViolation,
    assert_draft_clean,
    assert_sendable,
    assert_state_allows_send,
    check_draft,
    needs_escalation,
    risk_flags,
)

__all__ = [
    "FIELD_CATALOG",
    "NEVER_DISCLOSE",
    "DisclosureRefused",
    "PolicyViolation",
    "assert_draft_clean",
    "assert_sendable",
    "assert_state_allows_send",
    "check_draft",
    "detect",
    "diff_view",
    "fence",
    "minimize",
    "needs_escalation",
    "risk_flags",
    "sanitize",
    "screen",
    "screen_message",
    "scrub",
    "split_layers",
    "summarize",
]
