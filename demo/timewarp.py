"""The time-warp: six simulated weeks through the real pipeline.

The agent's value is that it operates for six weeks. The video is four minutes. This
resolves that honestly: the calendar is compressed and **nothing else is**.

Every letter goes through the real inbound screen, the real classifier, the real state
machine, the real approval gate and the real transport. No path is stubbed, no result is
pre-computed, and if you break the guardrails this run will show it rather than hide it.
What changes is only that `SimulatedClock.advance()` moves a day forward instead of a day
passing.

Each simulated day, in this order:

1. advance the clock
2. wake any sub-agent whose dormancy timer has come due
3. deliver the mail scheduled for that day
4. let the executor work through the approval queue

That order matters: a follow-up drafted by a timer on day 28 should be approvable on day
28, and mail arriving that morning should be answerable the same afternoon.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from packages.core.audit.sink import get_audit_log
from packages.core.logging import get_logger, set_level
from packages.core.models import CaseState, Verdict
from packages.core.repos import get_repos
from packages.core.telemetry import recorder
from services.inbox.handler import get_pipeline
from services.orchestrator.root import get_orchestrator

from demo import simclock
from demo.estate import SCRIPT, SIMULATION_DAYS
from demo.executor import SimulatedExecutor

log = get_logger("demo.timewarp")


@dataclass
class DayReport:
    day: int
    date: str
    delivered: int = 0
    blocked: int = 0
    woken: int = 0
    approved: int = 0
    closed: int = 0
    escalated: int = 0
    events: list[str] = field(default_factory=list)


def run(
    estate_id: str | None = None,
    *,
    days: int = SIMULATION_DAYS,
    factor: int = 400,
    quiet: bool = True,
    on_day=None,
) -> dict[str, Any]:
    if quiet:
        set_level("WARNING")

    repos = get_repos()
    estate = repos.estates.require(estate_id) if estate_id else repos.estates.current()
    if estate is None:
        raise RuntimeError("No estate found. Run `python tasks.py seed` first.")

    clock = simclock.install(fresh=False, factor=factor)
    orchestrator = get_orchestrator()
    # Idempotent for cases that already exist - it re-instantiates the sub-agent fleet on
    # this process's runtime so dormancy timers have something to wake.
    orchestrator.plan(estate.id)
    pipeline = get_pipeline()
    executor = SimulatedExecutor()

    start = clock.now()
    reports: list[DayReport] = []
    by_day: dict[int, list] = {}
    for event in SCRIPT:
        by_day.setdefault(event.day, []).append(event)

    for day in range(days + 1):
        clock.advance_to(start + timedelta(days=day))
        report = DayReport(day=day, date=clock.now().date().isoformat())

        report.woken = orchestrator.tick(clock.now())

        for event in by_day.get(day, []):
            message = pipeline.handle(
                estate.id,
                event.body,
                from_address=event.from_address,
                subject=event.subject,
                source="SCAN" if "[OCR-TEXT-LAYER" in event.body else "EMAIL",
                institution_hint=event.institution,
            )
            report.delivered += 1
            if message.screening and message.screening.verdict is Verdict.BLOCK:
                report.blocked += 1
                report.events.append(
                    f"BLOCKED {event.payload_id or ''} from {event.institution}".strip()
                )
            else:
                report.events.append(f"{event.kind} from {event.institution}")

        report.approved = executor.process(estate.id)

        snapshot = orchestrator.summary(estate.id)
        report.closed = snapshot.closed
        report.escalated = snapshot.escalated
        reports.append(report)
        simclock.save(clock)

        if on_day is not None:
            on_day(report, snapshot)

    summary = orchestrator.summary(estate.id)
    audit = get_audit_log()
    chain_ok, broken_at = audit.verify()
    spans = recorder().spans

    result = {
        "estate_id": estate.id,
        "days": days,
        "factor": factor,
        "simulated_from": start.date().isoformat(),
        "simulated_to": clock.now().date().isoformat(),
        "delivered": sum(r.delivered for r in reports),
        "blocked": sum(r.blocked for r in reports),
        "woken": sum(r.woken for r in reports),
        "approved": executor.decided,
        "discovered": summary.discovered,
        "surprises": summary.surprises,
        "closed": summary.closed,
        "escalated": summary.escalated,
        "in_flight": summary.in_flight,
        "pending_approval": summary.pending_approval,
        "recovered_usd": summary.recovered_usd,
        "audit_records": len(audit.for_estate(estate.id)),
        "audit_chain_ok": chain_ok,
        "audit_chain_broken_at": broken_at,
        "spans": len(spans),
        "by_state": summary.by_state,
        "reports": reports,
    }
    log.info(
        "timewarp.complete",
        **{k: v for k, v in result.items() if k not in {"reports", "by_state"}},
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay six simulated weeks of inbound mail.")
    parser.add_argument("--days", type=int, default=SIMULATION_DAYS)
    parser.add_argument("--factor", type=int, default=400, help="cosmetic speed shown in the UI")
    parser.add_argument("--verbose", action="store_true", help="print a line per simulated day")
    args = parser.parse_args()

    def printer(report: DayReport, snapshot) -> None:
        if not args.verbose and not report.events and not report.woken:
            return
        detail = "; ".join(report.events) or ("agents woken" if report.woken else "quiet")
        print(
            f"  day {report.day:>2}  {report.date}  "
            f"closed {snapshot.closed:>2}/{snapshot.discovered}  "
            f"escalated {snapshot.escalated}  "
            f"{detail}"
        )

    print()
    print(f"  Replaying six weeks of correspondence through the live pipeline at {args.factor}x")
    print()
    result = run(days=args.days, factor=args.factor, on_day=printer)
    print()
    print(f"  Simulated period  {result['simulated_from']} to {result['simulated_to']}")
    print(f"  Mail delivered    {result['delivered']} letters, {result['blocked']} blocked at the screen")
    print(f"  Sub-agent wakes   {result['woken']} from dormancy timers")
    print(f"  Approvals         {result['approved']} decided by the executor")
    print(f"  Outcome           {result['closed']} closed, {result['escalated']} escalated, "
          f"{result['in_flight']} still in flight")
    print(f"  Recovered         ${result['recovered_usd']:,.2f} the family would not have found")
    print(f"  Audit             {result['audit_records']} records, hash chain "
          f"{'VERIFIED' if result['audit_chain_ok'] else 'BROKEN'}")
    print(f"  Traces            {result['spans']} spans recorded")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
