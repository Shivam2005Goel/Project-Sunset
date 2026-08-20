"""State unclaimed-property lookup.

Billions of dollars sit in state unclaimed-property databases because the heirs never
knew an account existed. A dormant account is escheated to the state after a few years of
inactivity, and nothing about that process notifies the family - which is precisely the
kind of thing an agent with the decedent's name and last-known address can find in
seconds and a grieving executor never thinks to look for.

**Honest note about the implementation.** Real state registries expose search over an
HTML form with no documented API and rate limits that punish scraping. This module reads
a fixture that models three registries' response shape. `RegistrySource` is the seam: put
a live HTTP client behind it and nothing above changes. The demo says so on screen -
`docs/DEMO_SCRIPT.md` calls it "against a modelled registry", not "against California's
live database", because claiming the latter would be a lie a judge could check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from packages.core.logging import get_logger
from packages.core.offline import _normalize

log = get_logger("discovery.unclaimed")

FIXTURE = Path(__file__).resolve().parents[2] / "demo" / "corpus" / "unclaimed_registry.json"

# README section 10: three registries covered.
COVERED_REGISTRIES = ("CA", "OR", "NV")


@dataclass
class UnclaimedRecord:
    registry: str
    holder: str
    owner_name: str
    owner_address: str
    property_type: str
    amount_usd: float
    reported_year: int
    claim_reference: str
    claim_contact: str = ""

    @property
    def confidence(self) -> float:
        """Name-and-address matches are strong but not certain - people share names.

        Anything below 1.0 means the executor has to confirm identity before claiming,
        which the escalation brief says explicitly.
        """
        return 0.82


class RegistrySource(Protocol):
    def search(self, name: str, address: str) -> list[UnclaimedRecord]: ...


# Tokens that carry no locating power. "CA" matching "CA" is not an address match, and
# treating it as one returns every Halloran in the state.
GENERIC_ADDRESS_TOKENS = {
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln",
    "boulevard", "blvd", "court", "ct", "way", "place", "pl", "apt", "unit", "suite",
    "north", "south", "east", "west", "ca", "or", "nv", "tx", "usa", "us",
}


def _place_tokens(address: str) -> set[str]:
    """City and postcode, with street furniture and the state code removed."""
    tokens = set()
    for token in _normalize(address).split():
        if token in GENERIC_ADDRESS_TOKENS:
            continue
        if token.isdigit() and len(token) != 5:
            continue  # a house number locates nothing on its own
        tokens.add(token)
    return tokens


class FixtureRegistrySource:
    """Reads the modelled registry data that ships with the demo corpus."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or FIXTURE

    def _load(self) -> list[UnclaimedRecord]:
        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [UnclaimedRecord(**row) for row in raw.get("records", [])]

    def search(self, name: str, address: str) -> list[UnclaimedRecord]:
        wanted_name = set(_normalize(name).split())
        wanted_place = _place_tokens(address)
        matches = []
        for record in self._load():
            if record.registry not in COVERED_REGISTRIES:
                continue
            owner = set(_normalize(record.owner_name).split())
            # Surname plus at least one given name or initial. Loose enough for
            # "E. M. Halloran", tight enough not to return every Halloran in California.
            if len(wanted_name & owner) < 2:
                continue
            place = _place_tokens(record.owner_address)
            if wanted_place and not (wanted_place & place):
                continue
            matches.append(record)
        return matches


def search(name: str, address: str, source: RegistrySource | None = None) -> list[UnclaimedRecord]:
    source = source or FixtureRegistrySource()
    results = source.search(name, address)
    log.info(
        "unclaimed.search",
        name=name,
        registries=len(COVERED_REGISTRIES),
        matches=len(results),
        total_usd=round(sum(r.amount_usd for r in results), 2),
    )
    return results
