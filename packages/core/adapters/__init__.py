"""Adapters for Google's managed agent components.

**This directory is the only place a GEAP SDK may be imported.** `tests/test_policy.py`
enforces that by static analysis. If a service is waitlisted, unavailable, or has a
different API surface than the docs promised, you fix one file in here and keep shipping.

Every adapter follows the same shape:

    get_<name>_adapter(settings) -> Adapter    # reads one env var, returns an instance
    set_<name>_adapter(adapter)                # tests and the demo driver override here

and every one has a fallback that satisfies the same contract without the managed
service. In local mode the fallback is always chosen, regardless of environment, so a
local run can never accidentally reach a Google endpoint.
"""

from packages.core.adapters.guardrail import (
    GuardrailAdapter,
    get_guardrail_adapter,
    set_guardrail_adapter,
)
from packages.core.adapters.memory import MemoryAdapter, get_memory_adapter, set_memory_adapter
from packages.core.adapters.registry import (
    RegistryAdapter,
    bump,
    get_registry_adapter,
    parse_version,
    set_registry_adapter,
)
from packages.core.adapters.runtime import (
    AgentHandle,
    AgentSpec,
    RuntimeAdapter,
    get_runtime_adapter,
    service_account_for,
    set_runtime_adapter,
)

__all__ = [
    "AgentHandle",
    "AgentSpec",
    "GuardrailAdapter",
    "MemoryAdapter",
    "RegistryAdapter",
    "RuntimeAdapter",
    "bump",
    "get_guardrail_adapter",
    "get_memory_adapter",
    "get_registry_adapter",
    "get_runtime_adapter",
    "parse_version",
    "service_account_for",
    "set_guardrail_adapter",
    "set_memory_adapter",
    "set_registry_adapter",
    "set_runtime_adapter",
]


def reset_all_adapters() -> None:
    """Tests and `demo/seed.py` call this between runs."""
    set_guardrail_adapter(None)
    set_memory_adapter(None)
    set_registry_adapter(None)
    set_runtime_adapter(None)
