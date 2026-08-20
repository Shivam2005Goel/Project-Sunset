"""Typed repositories over the document store.

Services talk to these, never to the store directly, so that model validation happens on
the way in and on the way out. A malformed document fails at the boundary rather than
three services later.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

from packages.core import store as store_mod
from packages.core.models import (
    ApprovalRequest,
    ApprovalStatus,
    CaseState,
    ClosurePacket,
    Estate,
    InboundMessage,
    InstitutionCase,
    Obligation,
)
from packages.core.store import DocumentStore, get_store

M = TypeVar("M", bound=BaseModel)


class Repo(Generic[M]):
    collection: str
    model: type[M]

    def __init__(self, store: DocumentStore | None = None) -> None:
        self._store = store or get_store()

    def save(self, item: M) -> M:
        self._store.put(self.collection, item.id, item.model_dump(mode="json"))  # type: ignore[attr-defined]
        return item

    def get(self, item_id: str) -> M | None:
        raw = self._store.get(self.collection, item_id)
        return self.model.model_validate(raw) if raw else None

    def require(self, item_id: str) -> M:
        found = self.get(item_id)
        if found is None:
            raise KeyError(f"{self.collection}/{item_id} not found")
        return found

    def all(self) -> list[M]:
        return [self.model.model_validate(raw) for raw in self._store.all(self.collection)]

    def query(self, **equals) -> list[M]:
        return [
            self.model.model_validate(raw)
            for raw in self._store.query(self.collection, **equals)
        ]

    def delete(self, item_id: str) -> None:
        self._store.delete(self.collection, item_id)


class EstateRepo(Repo[Estate]):
    collection = store_mod.ESTATES
    model = Estate

    def current(self) -> Estate | None:
        """The demo has exactly one estate. Multi-estate is a product concern, not a
        hackathon one - but the data model already supports it."""
        estates = self.all()
        return estates[0] if estates else None


class ObligationRepo(Repo[Obligation]):
    collection = store_mod.OBLIGATIONS
    model = Obligation

    def for_estate(self, estate_id: str) -> list[Obligation]:
        return sorted(
            self.query(estate_id=estate_id),
            key=lambda o: (o.category.value, o.institution_name),
        )

    def surprises(self, estate_id: str) -> list[Obligation]:
        return [o for o in self.for_estate(estate_id) if o.is_surprise]


class CaseRepo(Repo[InstitutionCase]):
    collection = store_mod.CASES
    model = InstitutionCase

    def for_estate(self, estate_id: str) -> list[InstitutionCase]:
        return sorted(self.query(estate_id=estate_id), key=lambda c: c.institution_name)

    def in_state(self, estate_id: str, state: CaseState) -> list[InstitutionCase]:
        return [c for c in self.for_estate(estate_id) if c.state is state]

    def by_institution(self, estate_id: str, institution_id: str) -> InstitutionCase | None:
        found = self.query(estate_id=estate_id, institution_id=institution_id)
        return found[0] if found else None

    def due(self, estate_id: str, moment) -> list[InstitutionCase]:
        """Cases whose dormancy timer has expired - the sub-agents that should wake."""
        return [
            c
            for c in self.for_estate(estate_id)
            if c.next_wake_at is not None and c.next_wake_at <= moment and c.is_open
        ]


class PacketRepo(Repo[ClosurePacket]):
    collection = store_mod.PACKETS
    model = ClosurePacket

    def for_case(self, case_id: str) -> list[ClosurePacket]:
        return self.query(case_id=case_id)


class ApprovalRepo(Repo[ApprovalRequest]):
    collection = store_mod.APPROVALS
    model = ApprovalRequest

    def pending(self, estate_id: str) -> list[ApprovalRequest]:
        return sorted(
            [
                a
                for a in self.query(estate_id=estate_id)
                if a.status is ApprovalStatus.PENDING
            ],
            key=lambda a: a.created_at,
        )

    def for_packet(self, packet_id: str) -> ApprovalRequest | None:
        found = self.query(packet_id=packet_id)
        return found[0] if found else None


class InboundRepo(Repo[InboundMessage]):
    collection = store_mod.INBOUND
    model = InboundMessage

    def for_estate(self, estate_id: str) -> list[InboundMessage]:
        return sorted(self.query(estate_id=estate_id), key=lambda m: m.received_at)

    def blocked(self, estate_id: str) -> list[InboundMessage]:
        return [
            m
            for m in self.for_estate(estate_id)
            if m.screening is not None and m.screening.blocked
        ]


class Repos:
    """Bundle. Services take one of these instead of six constructor arguments."""

    def __init__(self, store: DocumentStore | None = None) -> None:
        store = store or get_store()
        self.store = store
        self.estates = EstateRepo(store)
        self.obligations = ObligationRepo(store)
        self.cases = CaseRepo(store)
        self.packets = PacketRepo(store)
        self.approvals = ApprovalRepo(store)
        self.inbound = InboundRepo(store)


def get_repos() -> Repos:
    return Repos()
