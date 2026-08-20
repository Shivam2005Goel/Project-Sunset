"""The discovery job.

A Cloud Run **Job**, not a service: it runs once per estate, reads the whole corpus, and
exits. Nothing about it needs to stay warm.

Output is the obligation graph - one normalized `Obligation` per institution, each
carrying evidence pointers back to the page it came from. No evidence, no obligation:
an entry the executor cannot trace back to a document is an entry they cannot act on.
"""

from __future__ import annotations

from pathlib import Path

from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.llm import get_llm
from packages.core.logging import get_logger
from packages.core.models import (
    DiscoveryMethod,
    Estate,
    Evidence,
    Obligation,
    ObligationCategory,
)
from packages.core.offline import _same_institution
from packages.core.repos import Repos, get_repos
from packages.core.telemetry import span
from services.discovery import unclaimed
from services.discovery.documents import SourceDocument, load_corpus
from services.discovery.inference import infer, slug

log = get_logger("discovery")


class DiscoveryJob:
    def __init__(self, repos: Repos | None = None, audit: AuditLog | None = None) -> None:
        self._repos = repos or get_repos()
        self._audit = audit or get_audit_log()

    def run(self, estate: Estate, corpus_dir: Path) -> list[Obligation]:
        with span("discovery.job", estate_id=estate.id, corpus=str(corpus_dir)) as job_span:
            documents = load_corpus(corpus_dir)
            self._audit.record(
                estate_id=estate.id,
                actor="discovery",
                action="discovery.started",
                reasoning=(
                    f"Reading {len(documents)} uploaded document(s) to reconstruct the "
                    f"obligation graph for {estate.decedent.full_name}."
                ),
                payload={"documents": [d.name for d in documents]},
            )

            documented, debits, credits = self._from_documents(estate, documents)
            inferred = self._from_inference(estate, documented, debits, credits)
            escheated = self._from_registries(estate)

            obligations = [*documented, *inferred, *escheated]
            for obligation in obligations:
                self._repos.obligations.save(obligation)

            surprises = [o for o in obligations if o.is_surprise]
            job_span.attributes.update(
                {"obligations": len(obligations), "surprises": len(surprises)}
            )

            self._audit.record(
                estate_id=estate.id,
                actor="discovery",
                action="discovery.completed",
                reasoning=(
                    f"Reconstructed {len(obligations)} obligations from "
                    f"{len(documents)} documents. {len(surprises)} of them were not on "
                    f"any list the family provided: "
                    f"{', '.join(o.institution_name for o in surprises)}."
                ),
                payload={
                    "total": len(obligations),
                    "documented": len(documented),
                    "inferred": len(inferred),
                    "registry": len(escheated),
                },
            )
            log.info(
                "discovery.complete",
                estate_id=estate.id,
                total=len(obligations),
                surprises=len(surprises),
            )
            return obligations

    # --- documents -------------------------------------------------------------------

    def _from_documents(
        self, estate: Estate, documents: list[SourceDocument]
    ) -> tuple[list[Obligation], list[dict], list[dict]]:
        found: list[Obligation] = []
        debits: list[dict] = []
        credits: list[dict] = []
        llm = get_llm()

        for document in documents:
            with span("discovery.parse", document=document.name, provenance=document.provenance):
                response = llm.complete(
                    "discovery.extract",
                    prompt=(
                        "Read this document from a deceased person's files. Identify the "
                        "institution that issued it, any account or policy identifier, the "
                        "balance or value if stated, and every recurring transaction line. "
                        "Return JSON with keys institutions, debits, credits.\n\n"
                        f"Document: {document.name} ({document.kind})\n\n{document.text}"
                    ),
                    inputs={"text": document.text, "document": document.name},
                    images=document.images or None,
                )

            debits.extend(response.data.get("debits", []))
            credits.extend(response.data.get("credits", []))

            if document.kind == "certificate":
                # A death certificate carries a registrar's letterhead, which the parser
                # will happily read as an institution. It is evidence about the estate,
                # not an obligation of it.
                continue

            for row in response.data.get("institutions", []):
                name = row["institution_name"]
                if any(_same_institution(name, existing.institution_name) for existing in found):
                    # Twelve months of statements from one bank is one obligation, not
                    # twelve. Merge on the institution, keep the extra evidence.
                    match = next(e for e in found if _same_institution(name, e.institution_name))
                    match.evidence.append(
                        Evidence(
                            source_document=document.name,
                            page=row.get("page", 1),
                            excerpt=row.get("evidence_excerpt", ""),
                            kind=row.get("evidence_kind", "letterhead"),
                        )
                    )
                    match.confidence = min(0.99, match.confidence + 0.01)
                    continue

                account = row.get("account_number")
                found.append(
                    Obligation(
                        estate_id=estate.id,
                        institution_id=slug(name),
                        institution_name=name,
                        category=ObligationCategory(row.get("category", "OTHER")),
                        account_fingerprint=Obligation.fingerprint(account) if account else None,
                        confidence=float(row.get("confidence", 0.8)),
                        discovery_method=DiscoveryMethod.DOCUMENT,
                        estimated_value_usd=row.get("estimated_value_usd"),
                        contact_email=row.get("contact_email"),
                        evidence=[
                            Evidence(
                                source_document=document.name,
                                page=row.get("page", 1),
                                excerpt=row.get("evidence_excerpt", ""),
                                kind=row.get("evidence_kind", "letterhead"),
                            )
                        ],
                    )
                )

        log.info("discovery.documents", institutions=len(found), debits=len(debits), credits=len(credits))
        return found, debits, credits

    # --- inference -------------------------------------------------------------------

    def _from_inference(
        self,
        estate: Estate,
        documented: list[Obligation],
        debits: list[dict],
        credits: list[dict],
    ) -> list[Obligation]:
        inferred = infer(
            estate_id=estate.id,
            debits=debits,
            credits=credits,
            known_institutions=[o.institution_name for o in documented],
        )
        for obligation in inferred:
            self._audit.record(
                estate_id=estate.id,
                institution_id=obligation.institution_id,
                actor="discovery",
                action="obligation.inferred",
                reasoning=obligation.notes or "Inferred from recurring transactions.",
                payload={
                    "institution": obligation.institution_name,
                    "confidence": obligation.confidence,
                    "evidence": [e.short() for e in obligation.evidence],
                },
            )
        return inferred

    # --- registries ------------------------------------------------------------------

    def _from_registries(self, estate: Estate) -> list[Obligation]:
        records = unclaimed.search(estate.decedent.full_name, estate.decedent.last_address)
        obligations: list[Obligation] = []
        for record in records:
            obligations.append(
                Obligation(
                    estate_id=estate.id,
                    institution_id=slug(f"{record.registry}-unclaimed-{record.holder}"),
                    institution_name=f"{record.holder} (via {record.registry} unclaimed property)",
                    category=ObligationCategory.UNCLAIMED_PROPERTY,
                    confidence=record.confidence,
                    discovery_method=DiscoveryMethod.REGISTRY,
                    estimated_value_usd=record.amount_usd,
                    contact_email=record.claim_contact,
                    account_fingerprint=record.claim_reference,
                    evidence=[
                        Evidence(
                            source_document=f"{record.registry} unclaimed property registry",
                            excerpt=(
                                f"{record.owner_name}, {record.owner_address} - "
                                f"{record.property_type}, ${record.amount_usd:,.2f}, "
                                f"reported {record.reported_year}"
                            ),
                            kind="registry_match",
                        )
                    ],
                    notes=(
                        f"Name and last-known-address match in the {record.registry} "
                        f"registry. Identity must be confirmed by the executor before "
                        f"claiming - people share names."
                    ),
                )
            )
            self._audit.record(
                estate_id=estate.id,
                institution_id=obligations[-1].institution_id,
                actor="discovery",
                action="obligation.registry_match",
                reasoning=(
                    f"${record.amount_usd:,.2f} held by {record.holder} was escheated to "
                    f"{record.registry} in {record.reported_year} and matches the "
                    f"decedent's name and last known address. Nobody notifies the family "
                    f"when this happens."
                ),
                payload={"amount_usd": record.amount_usd, "reference": record.claim_reference},
            )
        return obligations


def run_discovery(estate_id: str, corpus_dir: Path) -> list[Obligation]:
    repos = get_repos()
    estate = repos.estates.require(estate_id)
    return DiscoveryJob(repos).run(estate, corpus_dir)
