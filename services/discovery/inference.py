"""Inference - the part of discovery that finds what nobody listed.

Documents tell you about institutions that sent paper. The interesting ones are the
institutions that did not: a direct debit leaving the current account every month with no
statement, no letterhead, and no entry on the family's list.

The reasoning is deliberately simple and deliberately conservative, because a false
positive here means writing to a stranger about a death:

* three or more transactions to the same counterparty
* amounts stable within 25% (a subscription, not a shop)
* no uploaded document carries that counterparty's letterhead

Money moving *in* on that pattern is more valuable still - an annuity or a small pension
nobody knew was being paid.
"""

from __future__ import annotations

from typing import Any

from packages.core.llm import get_llm
from packages.core.logging import get_logger
from packages.core.models import (
    DiscoveryMethod,
    Evidence,
    Obligation,
    ObligationCategory,
)

log = get_logger("discovery.inference")

MIN_OCCURRENCES = 3


def infer(
    *,
    estate_id: str,
    debits: list[dict[str, Any]],
    credits: list[dict[str, Any]],
    known_institutions: list[str],
) -> list[Obligation]:
    response = get_llm().complete(
        "discovery.infer",
        prompt=(
            "You are reconstructing an estate's obligations. Below are recurring "
            "transactions from the deceased's accounts and the list of institutions "
            "already identified from uploaded documents. Identify counterparties that "
            "recur but are NOT in the known list - these are relationships the family "
            "did not know about. Be conservative: three or more occurrences, stable "
            "amounts. Return JSON with key 'inferred'.\n\n"
            f"Known institutions: {known_institutions}\n"
            f"Transactions: {len(debits)} debits, {len(credits)} credits."
        ),
        inputs={
            "debits": debits,
            "credits": credits,
            "known_institutions": known_institutions,
            "min_occurrences": MIN_OCCURRENCES,
        },
    )

    obligations: list[Obligation] = []
    for row in response.data.get("inferred", []):
        obligations.append(
            Obligation(
                estate_id=estate_id,
                institution_id=slug(row["institution_name"]),
                institution_name=row["institution_name"],
                category=ObligationCategory(row.get("category", "OTHER")),
                confidence=float(row.get("confidence", 0.7)),
                discovery_method=DiscoveryMethod.INFERENCE,
                recurring_amount_usd=row.get("recurring_amount_usd"),
                # A recurring credit is money the estate is owed; a recurring debit is
                # money it is losing. Both matter, and the annual figure is what makes
                # the executor act.
                estimated_value_usd=(
                    round(float(row.get("recurring_amount_usd") or 0) * 12, 2)
                    if row.get("direction") == "credit"
                    else None
                ),
                evidence=[
                    Evidence(
                        source_document=item["source_document"],
                        excerpt=item["excerpt"],
                        kind="debit_line",
                    )
                    for item in row.get("evidence", [])
                ],
                notes=row.get("reasoning", ""),
            )
        )

    log.info(
        "inference.complete",
        candidates=len(debits) + len(credits),
        inferred=len(obligations),
        model=response.model,
    )
    return obligations


def slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:48]
