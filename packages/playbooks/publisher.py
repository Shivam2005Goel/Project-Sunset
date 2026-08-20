"""Publishing, resolution, and amendment of institution playbooks.

Three jobs:

* **Publish** the catalog to the registry with semantic versions (`make publish-playbooks`).
* **Resolve** a playbook for an obligation, falling back to the generic template and
  saying so rather than pretending to know the institution.
* **Amend** - when a sub-agent hits a demand the playbook did not anticipate, propose a
  new version. This is the loop that makes the fleet smarter across estates, and it goes
  through the executor like everything else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from packages.core.adapters.registry import RegistryAdapter, VersionError, get_registry_adapter
from packages.core.clock import now
from packages.core.logging import get_logger
from packages.core.models import ObligationCategory, _id
from packages.core.store import AMENDMENTS, get_store
from packages.playbooks.schema import GENERIC_NAME, Playbook, generic_playbook

log = get_logger("playbooks")

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"


class AmendmentProposal(BaseModel):
    """A sub-agent's proposal that a playbook is wrong or incomplete."""

    id: str = Field(default_factory=lambda: _id("amd"))
    estate_id: str
    case_id: str
    playbook_name: str
    from_version: str
    proposed_version: str
    add_required_documents: list[str] = Field(default_factory=list)
    rationale: str = ""
    diff: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "PROPOSED"  # PROPOSED | PUBLISHED | REJECTED
    proposed_at: Any = Field(default_factory=now)
    approval_id: str | None = None


# --- catalog -------------------------------------------------------------------------


def load_catalog(directory: Path | None = None) -> list[Playbook]:
    directory = directory or CATALOG_DIR
    playbooks = []
    for path in sorted(directory.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        playbooks.append(Playbook.model_validate(document))
    return playbooks


def publish_all(
    registry: RegistryAdapter | None = None,
    directory: Path | None = None,
    *,
    include_generic: bool = True,
) -> list[str]:
    """Publish every catalog playbook. Idempotent: an already-published version is a
    no-op rather than an error, so `make publish-playbooks` is safe to re-run."""
    registry = registry or get_registry_adapter()
    published: list[str] = []

    for playbook in load_catalog(directory):
        try:
            ref = registry.publish(
                playbook.name,
                playbook.version,
                playbook.to_document(),
                notes=f"Catalog publish - {playbook.institution_name}",
            )
            published.append(ref)
        except VersionError:
            published.append(f"{playbook.name}@{playbook.version} (already published)")

    if include_generic:
        # Reported either way, so the return value has the same shape on a re-run as on a
        # first run - a caller counting refs should not have to know the difference.
        if registry.exists(GENERIC_NAME, "1.0.0"):
            published.append(f"{GENERIC_NAME}@1.0.0 (already published)")
        else:
            template = generic_playbook(ObligationCategory.OTHER, "Unspecified institution")
            registry.publish(
                GENERIC_NAME,
                "1.0.0",
                template.to_document(),
                notes="Fallback template for institutions without a dedicated playbook",
            )
            published.append(f"{GENERIC_NAME}@1.0.0")

    log.info("playbooks.published", count=len(published))
    return published


# --- resolution ----------------------------------------------------------------------


def resolve(
    institution_name: str,
    category: ObligationCategory,
    registry: RegistryAdapter | None = None,
) -> tuple[Playbook, str, bool]:
    """Find the playbook for an institution.

    Returns `(playbook, ref, is_specific)`. `is_specific` is False when the generic
    template was used - the orchestrator records that on the case so the dashboard can
    show which institutions are running on real knowledge and which are not.
    """
    registry = registry or get_registry_adapter()

    for name in registry.list_names():
        if name == GENERIC_NAME:
            continue
        try:
            document = registry.fetch(name, "latest")
        except KeyError:
            continue
        playbook = Playbook.from_document(document)
        if playbook.matches(institution_name):
            return playbook, f"{playbook.name}@{playbook.version}", True

    # Nothing specific. Use the generic template, keeping the registry's published
    # version so the audit record still points at something immutable.
    try:
        document = registry.fetch(GENERIC_NAME, "latest")
        template = Playbook.from_document(document)
        template.institution_name = institution_name
        template.category = category
        return template, f"{GENERIC_NAME}@{template.version}", False
    except KeyError:
        template = generic_playbook(category, institution_name)
        return template, f"{GENERIC_NAME}@{template.version} (unpublished)", False


# --- amendment -----------------------------------------------------------------------


def propose_amendment(
    *,
    estate_id: str,
    case_id: str,
    playbook: Playbook,
    demanded_documents: list[str],
    institution_name: str,
    registry: RegistryAdapter | None = None,
) -> AmendmentProposal | None:
    """A sub-agent met a demand the playbook did not list. Propose the fix.

    Returns None when there is nothing new to learn - most follow-ups are already in
    `known_follow_up_demands`, and a registry full of no-op versions is noise.
    """
    registry = registry or get_registry_adapter()

    if playbook.name == GENERIC_NAME:
        # A gap in the generic template is not a finding about an institution; it is a
        # sign this institution deserves its own playbook. Recorded, not versioned.
        log.info("amendment.skipped_generic", institution=institution_name)
        return None

    known = {d.lower() for d in playbook.required_documents}
    known |= {d.document.lower() for d in playbook.known_follow_up_demands}
    novel = [d for d in demanded_documents if d.lower() not in known]
    if not novel:
        return None

    from packages.core.llm import get_llm

    result = get_llm().complete(
        "amendment.propose",
        prompt=(
            f"An institution required documents its playbook does not list.\n"
            f"Institution: {institution_name}\n"
            f"Playbook: {playbook.name} v{playbook.version}\n"
            f"Currently required: {playbook.required_documents}\n"
            f"Newly demanded: {novel}\n"
            f"Return JSON: proposed_version, add_required_documents, rationale."
        ),
        inputs={
            "institution_name": institution_name,
            "current_version": playbook.version,
            "required_documents": playbook.required_documents,
            "demanded_documents": novel,
        },
    ).data

    amended = playbook.model_copy(deep=True)
    amended.version = result.get("proposed_version", playbook.version)
    amended.required_documents = [*playbook.required_documents, *result.get("add_required_documents", novel)]
    amended.notes = (playbook.notes + " ").strip() + (
        f"v{amended.version}: added {', '.join(novel)} after {institution_name} demanded it."
    )

    from packages.core.adapters.registry import diff_documents

    proposal = AmendmentProposal(
        estate_id=estate_id,
        case_id=case_id,
        playbook_name=playbook.name,
        from_version=playbook.version,
        proposed_version=amended.version,
        add_required_documents=novel,
        rationale=result.get("rationale", ""),
        diff=diff_documents(playbook.to_document(), amended.to_document()),
    )
    get_store().put(AMENDMENTS, proposal.id, proposal.model_dump(mode="json"))
    log.info(
        "amendment.proposed",
        playbook=playbook.name,
        from_version=playbook.version,
        to_version=amended.version,
        added=len(novel),
    )
    return proposal


def apply_amendment(
    proposal_id: str, registry: RegistryAdapter | None = None
) -> str:
    """Publish an approved amendment. Called only from the approvals service."""
    registry = registry or get_registry_adapter()
    store = get_store()
    raw = store.get(AMENDMENTS, proposal_id)
    if raw is None:
        raise KeyError(f"amendment {proposal_id} not found")
    proposal = AmendmentProposal.model_validate(raw)

    current = Playbook.from_document(registry.fetch(proposal.playbook_name, proposal.from_version))
    amended = current.model_copy(deep=True)
    amended.version = proposal.proposed_version
    amended.required_documents = [*current.required_documents, *proposal.add_required_documents]
    amended.notes = (current.notes + " ").strip() + (
        f"v{amended.version}: added {', '.join(proposal.add_required_documents)}."
    )

    ref = registry.publish(
        amended.name,
        amended.version,
        amended.to_document(),
        notes=proposal.rationale,
    )
    proposal.status = "PUBLISHED"
    store.put(AMENDMENTS, proposal.id, proposal.model_dump(mode="json"))
    log.info("amendment.published", ref=ref)
    return ref


def list_amendments(estate_id: str | None = None) -> list[AmendmentProposal]:
    rows = get_store().all(AMENDMENTS)
    proposals = [AmendmentProposal.model_validate(row) for row in rows]
    if estate_id:
        proposals = [p for p in proposals if p.estate_id == estate_id]
    return sorted(proposals, key=lambda p: str(p.proposed_at))
