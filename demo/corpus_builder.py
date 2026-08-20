"""Generates the uploaded corpus from `demo/estate.py`.

Two kinds of artifact:

* **Statements and letters** - what the executor uploads. Written as the text layer of a
  scanned page, which is what the pipeline actually consumes in local mode. The layout
  matters: discovery reads letterheads, account lines and transaction rows out of this
  with the same regex a Vision OCR response would be fed through.
* **Inbound correspondence** - the six weeks of replies, written to `demo/corpus/inbound/`
  so a judge can read the letters rather than take the demo's word for them.

Regenerating is idempotent and safe: `demo/estate.py` is the single source of truth, so
the files on disk and the script the time-warp replays can never drift apart.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from demo.estate import (
    CREDITS,
    DEBITS,
    DECEDENT,
    DOCUMENTED,
    SCRIPT,
    STATEMENT_PERIOD,
    UNCLAIMED_RECORDS,
    DemoInstitution,
    RecurringLine,
)

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
STATEMENTS_DIR = CORPUS_DIR / "statements"
INBOUND_DIR = CORPUS_DIR / "inbound"


def _months(count: int, step: int) -> list[date]:
    start_year, start_month = int(STATEMENT_PERIOD[0][:4]), int(STATEMENT_PERIOD[0][5:7])
    out: list[date] = []
    for index in range(0, count * step, step):
        month = start_month + index
        year = start_year + (month - 1) // 12
        out.append(date(year, (month - 1) % 12 + 1, 1))
    return out


def _transaction_rows() -> list[str]:
    """One year of activity, in date order.

    Column alignment is deliberate - two spaces minimum before the amount, because that
    is the separator the parser keys on, exactly as it would on a real statement.
    """
    rows: list[tuple[date, str]] = []
    for line in [*DEBITS, *CREDITS]:
        step = 12 // line.months if line.months < 12 else 1
        for month_start in _months(line.months, step):
            day = min(line.day_of_month, 28)
            when = date(month_start.year, month_start.month, day)
            if when.isoformat() > STATEMENT_PERIOD[1]:
                continue
            sign = "+" if line.kind == "ACH CREDIT" else "-"
            amount = f"{sign}{line.amount:,.2f}"
            rows.append(
                (
                    when,
                    f"{when.isoformat()}  {line.kind:<13} {line.merchant:<38}{amount:>12}",
                )
            )
    rows.sort(key=lambda pair: pair[0])
    return [row for _, row in rows]


def _statement(institution: DemoInstitution) -> str:
    lines = [
        "=== PAGE 1 ===",
        institution.name,
        institution.address,
        "",
        "STATEMENT OF ACCOUNT",
        institution.blurb,
        f"{institution.identifier_label}: {institution.identifier}",
        f"Statement Period: {STATEMENT_PERIOD[0]} to {STATEMENT_PERIOD[1]}",
        f"Account Holder: {DECEDENT['full_name'].upper()}",
        f"                {DECEDENT['last_address']}",
        "",
    ]
    if institution.transactions:
        lines += ["TRANSACTIONS", ""]
        lines += _transaction_rows()
        lines += [""]
    if institution.balance is not None:
        lines += [f"{institution.balance_label}: ${institution.balance:,.2f}", ""]
    lines += [
        "Questions about this statement? Write to " + institution.contact,
        "",
        "=== END OF DOCUMENT ===",
    ]
    return "\n".join(lines) + "\n"


def _letter(institution: DemoInstitution) -> str:
    lines = [
        "=== PAGE 1 ===",
        institution.name,
        institution.address,
        "",
        institution.blurb.upper(),
        f"{institution.identifier_label}: {institution.identifier}",
        "",
        f"{DECEDENT['full_name'].upper()}",
        f"{DECEDENT['last_address']}",
        "",
        "This notice is issued annually for your records. Please retain it. If any of the",
        "details shown are incorrect, contact us at the address below.",
        "",
    ]
    if institution.balance is not None:
        lines += [f"{institution.balance_label}: ${institution.balance:,.2f}", ""]
    lines += [
        "Correspondence: " + institution.contact,
        "",
        "=== END OF DOCUMENT ===",
    ]
    return "\n".join(lines) + "\n"


def _death_certificate() -> str:
    """Included in the corpus because the executor uploads it, and excluded from the
    obligation graph because a death certificate is not an obligation."""
    return "\n".join(
        [
            "=== PAGE 1 ===",
            "CERTIFICATE OF DEATH",
            "(FICTIONAL DOCUMENT - GENERATED FOR DEMONSTRATION PURPOSES ONLY)",
            "",
            f"Name of decedent: {DECEDENT['full_name'].upper()}",
            f"Date of birth: {DECEDENT['date_of_birth']}",
            f"Date of death: {DECEDENT['date_of_death']}",
            f"Last usual residence: {DECEDENT['last_address']}",
            "Registration number: D-2026-114882",
            "",
            "Cause of death is recorded on the registrar's copy and is deliberately",
            "omitted from this record. No institution's closure process requires it, and",
            "Aftercare will not disclose it to any recipient under any circumstances.",
            "",
            "=== END OF DOCUMENT ===",
        ]
    ) + "\n"


def build_statements(directory: Path | None = None) -> list[Path]:
    directory = directory or STATEMENTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.txt"):
        stale.unlink()

    written = [directory / "00-death-certificate.txt"]
    written[0].write_text(_death_certificate(), encoding="utf-8")

    for index, institution in enumerate(DOCUMENTED, start=1):
        renderer = _statement if institution.doc_kind == "statement" else _letter
        path = directory / f"{index:02d}-{institution.key}-{institution.doc_kind}.txt"
        path.write_text(renderer(institution), encoding="utf-8")
        written.append(path)
    return written


def build_inbound(directory: Path | None = None) -> list[Path]:
    """Write the scripted replies out so they can be read, not just replayed."""
    directory = directory or INBOUND_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.txt"):
        stale.unlink()

    written = []
    for event in SCRIPT:
        path = directory / f"day-{event.day:02d}-{event.institution}-{event.kind}.txt"
        header = [
            f"From: {event.from_address}",
            f"Subject: {event.subject}",
            f"Simulated-Day: {event.day}",
            f"Kind: {event.kind}",
        ]
        if event.payload_id:
            header.append(f"Adversarial-Payload: {event.payload_id}")
        if event.note:
            header.append(f"Note: {event.note}")
        path.write_text("\n".join(header) + "\n\n" + event.body + "\n", encoding="utf-8")
        written.append(path)
    return written


def build_registry_fixture(path: Path | None = None) -> Path:
    import json

    path = path or (CORPUS_DIR / "unclaimed_registry.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "_comment": (
                    "Models the response shape of three state unclaimed-property "
                    "registries. Fictional records only. See services/discovery/"
                    "unclaimed.py for what is and is not claimed about this."
                ),
                "records": UNCLAIMED_RECORDS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def build_all() -> dict[str, int]:
    statements = build_statements()
    inbound = build_inbound()
    build_registry_fixture()
    return {
        "statements": len(statements),
        "inbound": len(inbound),
        "unclaimed_records": len(UNCLAIMED_RECORDS),
    }


def recurring_summary() -> list[dict[str, object]]:
    """Used by the tests to assert the corpus really does hide three institutions."""
    return [
        {
            "merchant": line.merchant,
            "amount": line.amount,
            "documented": line.documented,
            "occurrences": line.months,
        }
        for line in [*DEBITS, *CREDITS]
    ]


def undocumented_lines() -> list[RecurringLine]:
    return [line for line in [*DEBITS, *CREDITS] if not line.documented]


if __name__ == "__main__":
    print(build_all())
