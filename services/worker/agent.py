"""The institution sub-agent.

One instance per institution, dormant between letters. Everything it knows about its case
lives in the FSM and the memory adapter, never in a context window - so waking up after
three weeks costs a store read, not a replay of a conversation.

Four things it does:

* draft the opening closure request from the playbook
* draft a follow-up when the institution asks for something more
* propose a playbook amendment when the demand was one the playbook did not anticipate
* escalate, with a one-paragraph brief, the moment judgement is required

It never sends. It puts drafts in the approval queue and goes back to sleep.
"""

from __future__ import annotations

from typing import Any

from packages.core.adapters.memory import get_memory_adapter
from packages.core.adapters.runtime import AgentHandle
from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.fsm import EstateFSM
from packages.core.llm import get_llm
from packages.core.logging import get_logger
from packages.core.models import (
    CaseState,
    Channel,
    Classification,
    ClosurePacket,
    CorrespondenceClass,
    Estate,
    InstitutionCase,
    Obligation,
    ObligationCategory,
)
from packages.core.repos import Repos, get_repos
from packages.core.telemetry import span
from packages.guardrails.pii import DisclosureRefused, minimize, scrub, summarize
from packages.guardrails.policy import needs_escalation
from packages.playbooks.publisher import propose_amendment, resolve
from packages.playbooks.schema import Playbook
from services.api.approvals import ApprovalService, get_approval_service

log = get_logger("worker")


def estate_facts(estate: Estate, obligation: Obligation) -> dict[str, Any]:
    """Everything the estate holds that could conceivably be disclosed.

    Assembling this is not the same as disclosing it - `minimize()` decides what leaves
    the building. Holding the full set here and filtering per recipient is what makes the
    withheld column of the approval view truthful.
    """
    is_policy = obligation.category in {
        ObligationCategory.LIFE_INSURANCE,
        ObligationCategory.PENSION,
    }
    return {
        "decedent_full_name": estate.decedent.full_name,
        "decedent_date_of_death": estate.decedent.date_of_death,
        "decedent_date_of_birth": estate.decedent.date_of_birth,
        "decedent_last_address": estate.decedent.last_address,
        "decedent_ssn_last4": estate.decedent.ssn_last4,
        "death_certificate_certified": "certified copy enclosed (registration D-2026-114882)",
        "death_certificate_number": "D-2026-114882",
        "letters_testamentary": f"issued {estate.jurisdiction} probate, ref {estate.executor.grant_reference}",
        "executor_full_name": estate.executor.full_name,
        "executor_email": estate.executor.email,
        "executor_phone": "(510) 555-0147",
        "executor_address": "218 Ridgeline Avenue, Oakland CA 94611",
        "executor_photo_id": "government-issued photo ID enclosed",
        # One identifier, two names for it depending on what kind of relationship this
        # is. Populating both from the same string would make the minimizer withhold a
        # "policy number" whose value is the account reference it just disclosed.
        "account_fingerprint": None if is_policy else obligation.account_fingerprint,
        "policy_number": obligation.account_fingerprint if is_policy else None,
        "estate_value": None,  # never assembled; the agent has no business totalling it
        "beneficiary_names": None,  # a legal determination, not a fact the agent holds
        "tax_identification_number": "EIN 88-8817204 (estate)",
        # Deliberately absent from every packet: cause_of_death, decedent_ssn_full,
        # account_number_full. They are on the never-disclose list and the estate record
        # does not carry them into the drafting layer at all.
    }


class InstitutionAgent:
    def __init__(
        self,
        repos: Repos | None = None,
        approvals: ApprovalService | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._repos = repos or get_repos()
        self._audit = audit or get_audit_log()
        self._approvals = approvals or get_approval_service()
        self._fsm = EstateFSM(self._repos.cases, self._audit)
        self._memory = get_memory_adapter()

    # --- drafting --------------------------------------------------------------------

    def draft(self, case: InstitutionCase, *, is_followup: bool = False) -> ClosurePacket:
        estate = self._repos.estates.require(case.estate_id)
        obligation = self._repos.obligations.require(case.obligation_id)
        playbook, ref, specific = resolve(case.institution_name, case.category)

        with span(
            "worker.draft",
            institution=case.institution_name,
            playbook=ref,
            followup=is_followup,
        ):
            facts = estate_facts(estate, obligation)
            try:
                disclosures = minimize(
                    facts,
                    required=playbook.required_disclosures,
                    recipient=case.institution_name,
                    playbook_name=playbook.name,
                )
            except DisclosureRefused as exc:
                # A playbook that demands a never-disclose field is not a drafting
                # problem - it is a decision for the executor. Boundary 4.
                self._escalate(
                    case,
                    trigger="playbook requires a never-disclose field",
                    detail=str(exc),
                )
                raise

            response = get_llm().complete(
                "packet.draft",
                prompt=_draft_prompt(playbook, estate, case, disclosures, is_followup),
                inputs={
                    "institution_name": case.institution_name,
                    "playbook": playbook.draft_context(),
                    "decedent": estate.decedent.model_dump(mode="json"),
                    "executor": estate.executor.model_dump(mode="json"),
                    "disclosures": [d.model_dump(mode="json") for d in disclosures],
                    # Not `obligation.account_fingerprint`. Reading the identifier
                    # straight off the obligation would route around the minimizer -
                    # the letter may only quote a reference this recipient is entitled
                    # to see.
                    "account_reference": _disclosed_reference(disclosures),
                    "outstanding_requests": case.outstanding_requests,
                    "is_followup": is_followup,
                },
            )

            body, removed = scrub(response.data.get("body", ""), disclosures)
            if removed:
                log.warning("draft.scrubbed", institution=case.institution_name, fields=removed)

            packet = ClosurePacket(
                estate_id=case.estate_id,
                case_id=case.id,
                institution_name=case.institution_name,
                recipient=playbook.submission_address or (obligation.contact_email or "unknown"),
                channel=playbook.submission_channel,
                subject=response.data.get("subject", f"Estate of {estate.decedent.full_name}"),
                body=body,
                disclosures=disclosures,
                attachments=[d.field for d in disclosures if d.disclosed and d.sensitivity.value in {"HIGH", "CRITICAL"}],
                playbook_ref=ref,
                model_used=response.model,
                reasoning=response.data.get("reasoning", ""),
            )
            self._repos.packets.save(packet)

            if case.state is CaseState.DISCOVERED or (
                is_followup and case.state is CaseState.INFO_REQUESTED
            ):
                self._fsm.transition(
                    case,
                    CaseState.PACKET_DRAFTED,
                    event="packet.drafted",
                    reason=(
                        f"Drafted {'a follow-up' if is_followup else 'the opening closure request'} "
                        f"to {case.institution_name} using {ref}"
                        + ("" if specific else " (generic template - no dedicated playbook published)")
                        + f". {response.data.get('reasoning', '')}"
                    ),
                    actor="worker",
                    payload={"packet_id": packet.id, "model": response.model},
                )

            summary = summarize(disclosures)
            self._memory.append(
                case.memory_key or case.id,
                {
                    "kind": "outbound_drafted",
                    "summary": f"Drafted {'follow-up' if is_followup else 'opening letter'} using {ref}",
                    "packet_id": packet.id,
                    "disclosed": summary["disclosed"],
                    "withheld": summary["withheld"],
                },
            )
            self._approvals.request_outbound(
                case,
                packet,
                disclosure_summary=summary,
                summary=(
                    f"{'Follow-up' if is_followup else 'Closure request'} to "
                    f"{case.institution_name} ({case.category.value.replace('_', ' ').title()})"
                ),
            )
            return packet

    # --- inbound handling ------------------------------------------------------------

    def on_correspondence(
        self,
        case: InstitutionCase,
        classification: Classification,
        *,
        sanitized_text: str,
        message_id: str,
    ) -> str:
        """Route a classified reply back into the state machine.

        Returns a short description of what happened, for the log and the UI.
        """
        playbook, _, _ = resolve(case.institution_name, case.category)

        escalate, reason = needs_escalation(
            classification_label=classification.label,
            text=sanitized_text,
            case=case,
            max_follow_ups=playbook.max_follow_ups,
        )
        if escalate:
            self._escalate(case, trigger=reason, detail=classification.reasoning)
            return f"escalated: {reason}"

        self._memory.append(
            case.memory_key or case.id,
            {
                "kind": "inbound",
                "summary": f"{classification.label.value}: {classification.reasoning[:200]}",
                "message_id": message_id,
                "requested_documents": classification.requested_documents,
            },
        )

        if classification.label is CorrespondenceClass.COMPLETION:
            return self._close(case, classification)
        if classification.label is CorrespondenceClass.DOCUMENT_REQUEST:
            return self._handle_document_request(case, classification, playbook)
        if classification.label is CorrespondenceClass.ACKNOWLEDGEMENT:
            case.next_wake_at = None
            self._repos.cases.save(case)
            self._audit.record(
                estate_id=case.estate_id,
                institution_id=case.institution_id,
                case_id=case.id,
                actor="worker",
                action="inbound.acknowledged",
                reasoning=(
                    f"{case.institution_name} acknowledged receipt. No action required; the "
                    f"sub-agent stays dormant and waits for the substantive reply."
                ),
                payload={"message_id": message_id},
            )
            return "acknowledged"

        # IRRELEVANT - marketing, rate notices, newsletters addressed to the deceased.
        self._audit.record(
            estate_id=case.estate_id,
            institution_id=case.institution_id,
            case_id=case.id,
            actor="worker",
            action="inbound.ignored",
            reasoning=f"Classified {classification.label.value}; not part of the closure process.",
            payload={"message_id": message_id},
        )
        return "ignored"

    def _handle_document_request(
        self, case: InstitutionCase, classification: Classification, playbook: Playbook
    ) -> str:
        case.outstanding_requests = classification.requested_documents
        case.follow_ups_sent += 1
        self._repos.cases.save(case)

        self._fsm.transition(
            case,
            CaseState.INFO_REQUESTED,
            event="inbound.document_request",
            reason=(
                f"{case.institution_name} requires "
                f"{', '.join(classification.requested_documents)} before proceeding"
                + (f" (deadline: {classification.deadline})" if classification.deadline else "")
                + ". Drafting a follow-up enclosing what was asked for."
            ),
            actor="worker",
        )

        # Turn the surprise into a permanent asset for every future estate.
        proposal = propose_amendment(
            estate_id=case.estate_id,
            case_id=case.id,
            playbook=playbook,
            demanded_documents=classification.requested_documents,
            institution_name=case.institution_name,
        )
        if proposal is not None:
            self._approvals.request_amendment(case, proposal)

        self.draft(case, is_followup=True)
        return f"follow-up drafted for {len(classification.requested_documents)} requested item(s)"

    def _close(self, case: InstitutionCase, classification: Classification) -> str:
        obligation = self._repos.obligations.get(case.obligation_id)
        # "Recovered" means money the family would not otherwise have found - an
        # escheated balance, an annuity nobody knew was paying in. Closing a bank account
        # the executor already knew about moves money, but it does not recover anything,
        # and counting it would inflate the number the demo puts on screen.
        recovered = 0.0
        if obligation and obligation.is_surprise and obligation.estimated_value_usd:
            recovered = obligation.estimated_value_usd
        case.recovered_amount_usd = recovered
        self._repos.cases.save(case)

        self._fsm.transition(
            case,
            CaseState.CLOSED,
            event="inbound.completion",
            reason=(
                f"{case.institution_name} confirmed the matter is complete. "
                f"{classification.reasoning}"
                + (f" Recovered ${recovered:,.2f} to the estate." if recovered else "")
            ),
            actor="worker",
        )
        return "closed"

    # --- escalation ------------------------------------------------------------------

    def _escalate(self, case: InstitutionCase, *, trigger: str, detail: str = "") -> None:
        playbook, _, _ = resolve(case.institution_name, case.category)
        response = get_llm().complete(
            "escalation.brief",
            prompt=(
                "Write a one-paragraph brief for the executor. State the situation, why it "
                "needs a human, and what you would recommend - then stop. Do not decide. "
                f"Institution: {case.institution_name}. Trigger: {trigger}. Detail: {detail}"
            ),
            inputs={
                "institution_name": case.institution_name,
                "category": case.category.value,
                "trigger": trigger,
                "detail": detail,
                "options": playbook.escalation_triggers[:3] or None,
            },
        )
        self._approvals.request_escalation(
            case,
            brief=response.data.get("brief", trigger),
            trigger=trigger,
            options=response.data.get("options", []),
        )

    # --- dormancy --------------------------------------------------------------------

    def on_wake(self, handle: AgentHandle, event: dict[str, Any]) -> str:
        """The runtime woke this agent. Rehydrate from the store and take one turn."""
        case_id = event.get("case_id")
        case = self._repos.cases.get(case_id) if case_id else None
        if case is None or not case.is_open:
            return "no-op"

        playbook, _, _ = resolve(case.institution_name, case.category)

        if event.get("type") == "follow_up_timer" and case.state is CaseState.AWAITING_RESPONSE:
            if case.follow_ups_sent >= playbook.max_follow_ups:
                self._escalate(
                    case,
                    trigger=(
                        f"{case.institution_name} has not responded after "
                        f"{case.follow_ups_sent} follow-ups"
                    ),
                    detail=f"Published SLA is {playbook.typical_sla_days} days.",
                )
                return "escalated after silence"

            case.follow_ups_sent += 1
            self._repos.cases.save(case)
            self._fsm.transition(
                case,
                CaseState.PACKET_DRAFTED,
                event="follow_up.timer",
                reason=(
                    f"No reply from {case.institution_name} within "
                    f"{playbook.follow_up_after_days} days of sending. Drafting follow-up "
                    f"{case.follow_ups_sent} of {playbook.max_follow_ups}."
                ),
                actor="worker",
            )
            self.draft(case, is_followup=True)
            return "follow-up drafted"

        return "no-op"


def _disclosed_reference(disclosures: list[Any]) -> str | None:
    """The account or policy reference, if this recipient is entitled to one."""
    for field in ("account_fingerprint", "policy_number"):
        for item in disclosures:
            if item.field == field and item.disclosed and item.value:
                return str(item.value)
    return None


def _draft_prompt(
    playbook: Playbook,
    estate: Estate,
    case: InstitutionCase,
    disclosures: list[Any],
    is_followup: bool,
) -> str:
    """The drafting prompt.

    Only *disclosed* fields appear. The withheld ones are not mentioned, not summarized,
    and not hinted at - a model cannot leak what it was never shown.
    """
    shown = "\n".join(
        f"  - {item.field}: {item.value}" for item in disclosures if item.disclosed
    )
    return (
        "Draft a letter from the executor of an estate to an institution, in the "
        "executor's voice. Never sign it. Never assert legal authority. Never state who "
        "inherits or whether a claim is valid. Request a process; do not demand an "
        "outcome.\n\n"
        f"Institution: {case.institution_name} ({case.category.value})\n"
        f"Department: {playbook.department}\n"
        f"Playbook: {playbook.name} v{playbook.version}\n"
        f"Their published requirements: {', '.join(playbook.required_documents)}\n"
        f"Their published turnaround: {playbook.typical_sla_days} days\n"
        f"{'This is a FOLLOW-UP. Outstanding requests: ' + ', '.join(case.outstanding_requests) if is_followup else 'This is the opening letter.'}\n\n"
        f"You may use ONLY these fields. Anything not listed here is withheld from this "
        f"recipient by the disclosure policy and must not appear:\n{shown}\n\n"
        "Return JSON with keys: subject, body, reasoning."
    )


_agent: InstitutionAgent | None = None


def get_agent() -> InstitutionAgent:
    global _agent
    if _agent is None:
        _agent = InstitutionAgent()
    return _agent


def set_agent(agent: InstitutionAgent | None) -> None:
    global _agent
    _agent = agent


def register(runtime) -> None:
    """Wire the wake handler into whichever runtime adapter is active."""
    runtime.register_handler("institution_agent", lambda handle, event: get_agent().on_wake(handle, event))
