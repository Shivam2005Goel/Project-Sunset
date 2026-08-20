"""Institution playbooks: schema, catalog, registry publisher, amendment path.

A playbook is the reusable asset in this system. The obligation graph is rebuilt for
every estate; the playbook is encoded once and improves every time an institution
surprises it.
"""

from packages.playbooks.publisher import (
    CATALOG_DIR,
    AmendmentProposal,
    apply_amendment,
    list_amendments,
    load_catalog,
    propose_amendment,
    publish_all,
    resolve,
)
from packages.playbooks.schema import GENERIC_NAME, FollowUpDemand, Playbook, generic_playbook

__all__ = [
    "CATALOG_DIR",
    "GENERIC_NAME",
    "AmendmentProposal",
    "FollowUpDemand",
    "Playbook",
    "apply_amendment",
    "generic_playbook",
    "list_amendments",
    "load_catalog",
    "propose_amendment",
    "publish_all",
    "resolve",
]
