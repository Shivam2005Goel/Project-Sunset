"""Seed the fictional estate: corpus, playbooks, discovery, fleet, opening drafts.

Leaves the system in the state the demo opens on - 23 obligations discovered, 23
sub-agents instantiated, 23 drafts sitting in the approval queue and nothing sent. That
last clause is the interesting one: seeding produces a full estate and zero outbound
communications, because seeding cannot approve anything.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from packages.core import store as store_mod
from packages.core.adapters import reset_all_adapters
from packages.core.audit.sink import set_audit_log
from packages.core.config import get_settings
from packages.core.llm import get_llm, set_llm
from packages.core.logging import get_logger, set_level
from packages.core.models import Decedent, Estate, Executor
from packages.core.repos import get_repos
from packages.core.store import set_store
from packages.playbooks.publisher import publish_all
from services.discovery.job import DiscoveryJob
from services.inbox.handler import set_pipeline
from services.orchestrator.root import get_orchestrator, set_orchestrator
from services.worker.agent import set_agent

from demo import simclock
from demo.corpus_builder import CORPUS_DIR, STATEMENTS_DIR, build_all
from demo.estate import DECEDENT, EXECUTOR

log = get_logger("demo.seed")


def reset_state() -> None:
    """Wipe local state and every module-level singleton that caches it."""
    settings = get_settings()
    if settings.data_dir.exists():
        shutil.rmtree(settings.data_dir, ignore_errors=True)
    settings.ensure_dirs()

    set_store(None)
    set_audit_log(None)
    set_llm(None)
    set_agent(None)
    set_pipeline(None)
    set_orchestrator(None)
    reset_all_adapters()

    from services.api.approvals import set_approval_service

    set_approval_service(None)


def create_estate() -> Estate:
    estate = Estate(
        decedent=Decedent(**DECEDENT),
        executor=Executor(**EXECUTOR),
        jurisdiction="CA",
        fictional=True,
        notes=(
            "Fabricated estate for demonstration. No part of this record refers to a real "
            "person, a real institution, or a real account."
        ),
    )
    get_repos().estates.save(estate)
    return estate


def seed(*, fresh: bool = True, corpus_dir: Path | None = None, quiet: bool = False) -> dict:
    if quiet:
        set_level("WARNING")
    if fresh:
        reset_state()

    # The whole estate lives on the simulated calendar, starting the morning after the
    # death. Seeding under wall time and then winding the clock back would produce a
    # case history that runs backwards.
    clock = simclock.install(fresh=fresh)

    # Prove the model is reachable before building anything. A misconfigured project
    # discovered on call 12 leaves a half-built estate and a stack trace; discovered
    # here it costs two seconds and prints what to do about it.
    get_llm().preflight()

    counts = build_all()
    estate = create_estate()
    published = publish_all()

    obligations = DiscoveryJob().run(estate, corpus_dir or STATEMENTS_DIR)
    orchestrator = get_orchestrator()
    cases = orchestrator.plan(estate.id)
    drafted = orchestrator.draft_all(estate.id)

    summary = orchestrator.summary(estate.id)
    simclock.save(clock)

    result = {
        "estate_id": estate.id,
        "decedent": estate.decedent.full_name,
        "documents": counts["statements"],
        "inbound_letters": counts["inbound"],
        "playbooks": len(published),
        "obligations": len(obligations),
        "surprises": summary.surprises,
        "cases": len(cases),
        "drafted": drafted,
        "pending_approval": summary.pending_approval,
        "provider": get_llm().provider_name,
        "simulated_date": clock.now().date().isoformat(),
        "corpus_dir": str(corpus_dir or STATEMENTS_DIR),
    }
    log.info("seed.complete", **{k: v for k, v in result.items() if k != "corpus_dir"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the fictional Aftercare estate.")
    parser.add_argument("--keep", action="store_true", help="do not wipe existing state")
    parser.add_argument("--quiet", action="store_true", help="warnings only")
    args = parser.parse_args()

    result = seed(fresh=not args.keep, quiet=args.quiet)

    surprises = result["surprises"]
    print()
    print(f"  Estate            {result['estate_id']}  ({result['decedent']}, fictional)")
    print(f"  Simulated date    {result['simulated_date']}")
    print(f"  Model provider    {result['provider']}")
    print(f"  Corpus            {result['documents']} documents, {result['inbound_letters']} scripted replies")
    print(f"  Playbooks         {result['playbooks']} published to the registry")
    print(f"  Obligations       {result['obligations']} discovered, {surprises} of them nobody listed")
    print(f"  Fleet             {result['cases']} institution sub-agents instantiated")
    print(f"  Approval queue    {result['pending_approval']} drafts awaiting the executor")
    print()
    print("  Nothing has been sent. Run `python tasks.py demo` to replay six weeks of mail.")
    print()

    for name in (store_mod.OBLIGATIONS, store_mod.CASES):
        assert get_repos().store.count(name) == result["obligations"], f"{name} count mismatch"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


CORPUS = CORPUS_DIR
