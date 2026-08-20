"""The offline deterministic planner.

This is **not a model**. It is rule-based code that satisfies the same task contracts the
Gemini providers satisfy, so that a cold clone with no API key and no GCP project can run
the entire agent loop - discovery, drafting, classification, escalation - and get a
truthful result rather than a canned one.

Everything it produces is labelled `offline-deterministic` at the call site, and the
dashboard renders that label next to any artifact it touched. The rule is simple: the
demo may run without a model, but it may never *pretend* to have used one.

Handlers are keyed by task name and registered in `HANDLERS` at the bottom.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# --- shared vocabulary ---------------------------------------------------------------

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("BANK", ("bank", "trust", "credit union", "savings", "bancorp", "federal cu")),
    ("BROKERAGE", ("securities", "brokerage", "investments", "capital markets", "asset management")),
    ("LIFE_INSURANCE", ("life", "assurance", "insurance", "insurers", "mutual life")),
    ("PENSION", ("pension", "retirement", "annuity", "superannuation", "401k")),
    ("UTILITY", ("energy", "power", "electric", "water", "gas", "grid", "utilities", "sanitation")),
    ("TELECOM", ("wireless", "mobile", "telecom", "broadband", "fiber", "cable", "communications")),
    ("SUBSCRIPTION", ("magazine", "review", "quarterly", "media", "streaming", "club", "society", "press")),
    ("CREDIT_CARD", ("card services", "cardmember", "credit card", "charge card")),
    ("MORTGAGE", ("mortgage", "home loan", "lending", "loan servicing")),
    ("GOVERNMENT", ("department of", "administration", "bureau", "county", "state of", "internal revenue")),
]

DOCUMENT_NOISE = {
    "statement of account",
    "statement",
    "account summary",
    "transactions",
    "page",
    "important information",
    "customer service",
    "notice",
}


def categorize(name: str) -> str:
    lowered = name.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "OTHER"


# --- discovery.extract ---------------------------------------------------------------

ACCOUNT_RE = re.compile(
    r"(?:account|acct|a/c)\s*(?:number|no\.?|#)?\s*[:\-]?\s*([*x\d][\d*x\- ]{3,22})",
    re.IGNORECASE,
)
POLICY_RE = re.compile(
    r"(?:policy|certificate|member|plan)\s*(?:number|no\.?|#|id)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{3,20})",
    re.IGNORECASE,
)
DEBIT_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s+"
    r"(DIRECT DEBIT|ACH DEBIT|CARD PURCHASE|RECURRING|AUTOPAY|STANDING ORDER)\s+"
    r"(.+?)\s{2,}-?\$?([\d,]+\.\d{2})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CREDIT_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s+(ACH CREDIT|DEPOSIT|DIVIDEND|INTEREST)\s+(.+?)\s{2,}\+?\$?([\d,]+\.\d{2})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
BALANCE_RE = re.compile(
    r"(?:closing balance|balance|current value|account value|death benefit|face amount)\s*[:\-]?\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _letterhead(text: str) -> str | None:
    """The institution's own name, printed at the top of its own paper."""
    for raw in text.splitlines()[:12]:
        line = raw.strip().strip("=").strip()
        if not line or len(line) < 4:
            continue
        if line.lower() in DOCUMENT_NOISE or line.upper().startswith("PAGE "):
            continue
        letters = [ch for ch in line if ch.isalpha()]
        if not letters:
            continue
        # A letterhead is set in caps or title case and is short. A sentence is not.
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        if upper_ratio > 0.7 and len(line) < 60 and not line.endswith((".", ":")):
            return " ".join(word.capitalize() if word.isupper() else word for word in line.split())
    return None


def _money(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_from_document(inputs: dict[str, Any]) -> dict[str, Any]:
    """Pull institutions, accounts and recurring debits out of one document.

    In cloud mode Gemini does this over the page images; here it is regex over the OCR
    text that ships with the corpus. Same output contract either way.
    """
    text: str = inputs.get("text", "")
    doc_name: str = inputs.get("document", "unknown")

    issuer = _letterhead(text)
    findings: list[dict[str, Any]] = []

    if issuer:
        account = ACCOUNT_RE.search(text)
        policy = POLICY_RE.search(text)
        balance = BALANCE_RE.search(text)
        contact = EMAIL_RE.search(text)
        identifier = (account.group(1) if account else None) or (policy.group(1) if policy else None)
        findings.append(
            {
                "institution_name": issuer,
                "category": categorize(issuer),
                "account_number": identifier.strip() if identifier else None,
                "confidence": 0.97 if identifier else 0.82,
                "evidence_kind": "letterhead" if not policy else "policy_number",
                "evidence_excerpt": (issuer + (f" - {identifier.strip()}" if identifier else "")),
                "estimated_value_usd": _money(balance.group(1)) if balance else None,
                "contact_email": contact.group(0) if contact else None,
                "page": 1,
            }
        )

    debits: list[dict[str, Any]] = []
    for match in DEBIT_RE.finditer(text):
        date, kind, merchant, amount = match.groups()
        debits.append(
            {
                "date": date,
                "kind": kind.upper(),
                "merchant": " ".join(merchant.split()).title(),
                "amount": _money(amount),
                "source_document": doc_name,
                "excerpt": " ".join(match.group(0).split()),
            }
        )

    credits: list[dict[str, Any]] = []
    for match in CREDIT_RE.finditer(text):
        date, kind, payer, amount = match.groups()
        credits.append(
            {
                "date": date,
                "kind": kind.upper(),
                "merchant": " ".join(payer.split()).title(),
                "amount": _money(amount),
                "source_document": doc_name,
                "excerpt": " ".join(match.group(0).split()),
            }
        )

    return {"institutions": findings, "debits": debits, "credits": credits}


# --- discovery.infer -----------------------------------------------------------------


def _normalize(name: str) -> str:
    lowered = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    stop = {"inc", "llc", "ltd", "co", "corp", "company", "the", "of", "and", "plc", "na", "usa"}
    return " ".join(word for word in lowered.split() if word not in stop)


def _same_institution(a: str, b: str) -> bool:
    left, right = set(_normalize(a).split()), set(_normalize(b).split())
    if not left or not right:
        return False
    if left & right and (left <= right or right <= left):
        return True
    overlap = len(left & right) / min(len(left), len(right))
    return overlap >= 0.6


def infer_hidden_obligations(inputs: dict[str, Any]) -> dict[str, Any]:
    """The demo's surprise.

    A relationship the family never listed and no statement documents still leaves a
    trace: money leaves the account on a schedule. Three or more debits to the same payee
    with a stable amount is a live obligation, and if no uploaded document carries that
    payee's letterhead, nobody knows about it.
    """
    debits: list[dict[str, Any]] = inputs.get("debits", [])
    credits: list[dict[str, Any]] = inputs.get("credits", [])
    known: list[str] = inputs.get("known_institutions", [])
    min_occurrences: int = int(inputs.get("min_occurrences", 3))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in [*debits, *credits]:
        grouped[entry["merchant"]].append(entry)

    inferred: list[dict[str, Any]] = []
    for merchant, entries in sorted(grouped.items()):
        if len(entries) < min_occurrences:
            continue
        if any(_same_institution(merchant, name) for name in known):
            continue  # already documented; not a surprise

        amounts = [e["amount"] for e in entries]
        spread = (max(amounts) - min(amounts)) / max(amounts) if max(amounts) else 1.0
        recurring = spread <= 0.25
        if not recurring:
            continue

        is_credit = any(e["kind"] in {"ACH CREDIT", "DEPOSIT", "DIVIDEND", "INTEREST"} for e in entries)
        median = sorted(amounts)[len(amounts) // 2]
        inferred.append(
            {
                "institution_name": merchant,
                # Money arriving on a schedule from an unnamed counterparty is almost
                # always a pension or annuity. Money leaving is a subscription if it is
                # small and a service account if it is not - guessing "subscription" for
                # a $148 monthly debit would put a storage unit in the wrong queue.
                "category": categorize(merchant)
                if categorize(merchant) != "OTHER"
                else ("PENSION" if is_credit else ("SUBSCRIPTION" if median < 50 else "OTHER")),
                "confidence": round(min(0.94, 0.55 + 0.08 * len(entries)), 2),
                "recurring_amount_usd": median,
                "occurrences": len(entries),
                "direction": "credit" if is_credit else "debit",
                "evidence": [
                    {
                        "source_document": e["source_document"],
                        "excerpt": e["excerpt"],
                        "kind": "debit_line",
                    }
                    for e in entries[:3]
                ],
                "reasoning": (
                    f"{len(entries)} {'credits' if is_credit else 'debits'} to '{merchant}' "
                    f"averaging ${median:,.2f}, stable within {spread:.0%}, with no uploaded "
                    f"document naming this counterparty. Treated as a live relationship the "
                    f"family did not list."
                ),
            }
        )
    return {"inferred": inferred}


# --- inbound.classify ----------------------------------------------------------------

DOCUMENT_TERMS = [
    # Named forms first, and matched on their exact identifier. A bare "form " needle
    # would make every one of these look like a generic claim form as well, and the
    # duplicate would then read as a document the playbook does not know about - which
    # proposes a playbook amendment for something already in the playbook.
    ("completed deceased account notification form (Form DA-2)", ("form da-2",)),
    ("completed claimant's statement (Form CL-1)", ("form cl-1",)),
    ("completed member bereavement notification (Form MB-7)", ("form mb-7",)),
    ("completed estate transfer instruction (Form ET-3)", ("form et-3",)),
    ("certified copy of the death certificate", ("certified copy", "certified death certificate")),
    ("letters testamentary", ("letters testamentary", "grant of probate", "letters of administration")),
    ("executor photo identification", ("photo id", "photo identification", "government-issued id", "proof of identity")),
    ("small estate affidavit", ("small estate affidavit", "affidavit of heirship")),
    ("completed claim form", ("claim form", "beneficiary form")),
    ("account closure authorisation", ("closure authorisation", "closure authorization", "signed authorisation")),
    ("proof of address", ("proof of address", "utility bill as proof")),
    ("tax identification number", ("tin", "taxpayer identification", "w-9")),
]

CLASS_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    (
        "COMPLETION",
        (
            "has been closed", "account is now closed", "we have closed",
            "claim has been paid", "payment has been issued", "settled in full",
            "final balance has been released", "policy has been paid out",
            "disbursement has been made", "matter is now concluded",
            "has been cancelled", "has been canceled", "no further action is required",
            "nothing further is owed", "has been disconnected", "has been ended",
        ),
    ),
    (
        "REJECTION",
        (
            "unable to proceed", "cannot process", "we are unable", "declined",
            "does not meet", "insufficient authority", "not authorised", "not authorized",
            "rejected", "we must decline",
        ),
    ),
    (
        "DOCUMENT_REQUEST",
        (
            "we require", "please provide", "please supply", "in order to proceed",
            "before we can", "we will need", "kindly forward", "must be accompanied by",
            "please submit",
        ),
    ),
    (
        "ACKNOWLEDGEMENT",
        (
            "we have received", "thank you for your letter", "acknowledge receipt",
            "your request is being processed", "reference number", "is under review",
            "we are sorry for your loss", "our condolences",
        ),
    ),
    (
        "IRRELEVANT",
        (
            "monthly newsletter", "marketing", "unsubscribe", "special offer",
            "rate change notice", "promotional",
        ),
    ),
]

DEADLINE_RE = re.compile(
    r"(?:within|by|before|no later than)\s+((?:\d{1,3}\s+(?:business\s+)?days)|(?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}\s+\w+\s+\d{4}))",
    re.IGNORECASE,
)


def classify_correspondence(inputs: dict[str, Any]) -> dict[str, Any]:
    """Classify one sanitized inbound letter.

    Receives the *sanitized* projection only. Invariant 3: raw inbound bytes never reach
    this function, and never reach a model in cloud mode either.
    """
    text: str = (inputs.get("sanitized_text") or "").lower()
    subject: str = (inputs.get("subject") or "").lower()
    haystack = f"{subject}\n{text}"

    scores: dict[str, int] = defaultdict(int)
    hits: dict[str, list[str]] = defaultdict(list)
    for label, phrases in CLASS_SIGNALS:
        for phrase in phrases:
            if phrase in haystack:
                scores[label] += 1
                hits[label].append(phrase)

    # A letter that both acknowledges and asks for documents is a document request -
    # that is the action the estate has to take.
    if scores.get("DOCUMENT_REQUEST") and scores.get("ACKNOWLEDGEMENT"):
        scores["DOCUMENT_REQUEST"] += 2

    if not scores:
        label, confidence, matched = "UNKNOWN", 0.3, []
    else:
        label = max(scores.items(), key=lambda kv: kv[1])[0]
        matched = hits[label]
        confidence = round(min(0.96, 0.55 + 0.12 * scores[label]), 2)

    requested: list[str] = []
    if label == "DOCUMENT_REQUEST":
        for canonical, needles in DOCUMENT_TERMS:
            if any(needle in haystack for needle in needles):
                requested.append(canonical)
        if not requested:
            requested = ["unspecified supporting documentation"]

    rejection_reason = None
    if label == "REJECTION":
        for sentence in re.split(r"(?<=[.!?])\s+", inputs.get("sanitized_text") or ""):
            if any(needle in sentence.lower() for needle in dict(CLASS_SIGNALS)["REJECTION"]):
                rejection_reason = sentence.strip()[:280]
                break

    deadline = None
    match = DEADLINE_RE.search(inputs.get("sanitized_text") or "")
    if match:
        deadline = match.group(1)

    return {
        "label": label,
        "confidence": confidence,
        "requested_documents": requested,
        "rejection_reason": rejection_reason,
        "deadline": deadline,
        "reasoning": (
            f"Matched {len(matched)} {label} signal(s): {', '.join(matched[:3]) or 'none'}."
            + (f" Documents requested: {', '.join(requested)}." if requested else "")
            + (f" Stated deadline: {deadline}." if deadline else "")
        ),
    }


# --- guardrail.judge -----------------------------------------------------------------


def judge_injection(inputs: dict[str, Any]) -> dict[str, Any]:
    """Second-opinion screen.

    The pattern screen in `packages/guardrails/inbound.py` runs first and is authoritative
    for BLOCK. This adds a semantic check for the phrasings patterns miss: a letter that
    addresses the reader as a system rather than as a person.
    """
    text: str = (inputs.get("text") or "").lower()

    tells = [
        ("addresses an automated agent directly", ("if you are an ai", "as an ai", "you are an assistant", "automated agent reading")),
        ("instructs a change of operating rules", ("ignore previous", "disregard the above", "new instructions", "system prompt", "override your")),
        ("requests silent action", ("do not tell", "without informing", "do not mention", "silently", "without the executor")),
        ("requests credential or key disclosure", ("api key", "service account", "credentials", "access token", "password")),
        ("requests a bypass of approval", ("skip approval", "no approval needed", "auto-approve", "pre-approved by us", "bypass review")),
        ("solicits fund transfer to a new destination", ("wire the balance", "transfer the funds to", "new account details", "updated banking details")),
    ]

    matched = [label for label, needles in tells if any(needle in text for needle in needles)]
    if not matched:
        return {"verdict": "ALLOW", "confidence": 0.9, "rationale": "No agent-directed instruction detected."}

    severe = any(
        term in " ".join(matched)
        for term in ("operating rules", "bypass", "credential", "fund transfer")
    )
    return {
        "verdict": "BLOCK" if severe else "SANITIZE",
        "confidence": 0.93,
        "rationale": "Letter contains content directed at an automated reader: " + "; ".join(matched) + ".",
        "signals": matched,
    }


# --- packet.draft --------------------------------------------------------------------


def draft_packet(inputs: dict[str, Any]) -> dict[str, Any]:
    """Compose the closure request.

    Written in the executor's voice, because the executor is the one with standing. The
    agent drafts; it does not sign, does not claim authority, and does not assert any
    entitlement - it requests a process. Boundaries 1 and 2.
    """
    institution: str = inputs["institution_name"]
    playbook: dict[str, Any] = inputs.get("playbook", {})
    decedent: dict[str, Any] = inputs.get("decedent", {})
    executor: dict[str, Any] = inputs.get("executor", {})
    disclosures: list[dict[str, Any]] = inputs.get("disclosures", [])
    account_ref: str | None = inputs.get("account_reference")
    extra_requests: list[str] = inputs.get("outstanding_requests", [])
    is_followup: bool = bool(inputs.get("is_followup"))

    department = playbook.get("department") or "Bereavement Services"
    required = list(playbook.get("required_documents", []))
    channel_note = playbook.get("submission_note", "")
    sla_days = playbook.get("typical_sla_days")

    enclosures = [d["field"] for d in disclosures if d.get("disclosed")]
    withheld = [d["field"] for d in disclosures if not d.get("disclosed")]

    subject = (
        f"{'Follow-up: ' if is_followup else ''}Notification of death and request to close "
        f"{'account ' + account_ref if account_ref else 'the account'} - "
        f"{decedent.get('full_name', 'the deceased')}"
    )

    lines = [
        f"To: {department}, {institution}",
        "",
        "Dear Sir or Madam,",
        "",
        f"I am writing in my capacity as executor of the estate of "
        f"{decedent.get('full_name', 'the deceased')}, who died on "
        f"{decedent.get('date_of_death', 'the date shown on the enclosed certificate')}.",
    ]

    if account_ref:
        lines += ["", f"The relationship I am writing about is identified in your records as {account_ref}."]

    if is_followup and extra_requests:
        lines += [
            "",
            "Further to my earlier letter, you asked for the following before you could "
            "proceed. Each item is enclosed:",
            *[f"  - {item}" for item in extra_requests],
        ]
    else:
        lines += [
            "",
            "I am asking you to record the death, stop any further charges or activity on "
            "the relationship, and tell me what your process requires from me to bring it "
            "to a close.",
        ]
        if required:
            lines += ["", "In anticipation, I enclose the documents your published process lists:", *[f"  - {item}" for item in required]]

    if enclosures:
        lines += ["", "Enclosed with this letter:", *[f"  - {item}" for item in enclosures]]

    if withheld:
        lines += [
            "",
            "I have not enclosed the following, as your stated process does not require it: "
            + ", ".join(withheld)
            + ". I will provide it promptly if you tell me why it is needed.",
        ]

    if sla_days:
        lines += ["", f"I understand your usual response time is around {sla_days} days. I would be grateful for written confirmation of receipt."]

    if channel_note:
        lines += ["", channel_note]

    lines += [
        "",
        "Please direct all correspondence about this estate to me at the address below.",
        "",
        "Yours faithfully,",
        executor.get("full_name", "Executor of the estate"),
        f"Executor of the estate of {decedent.get('full_name', 'the deceased')}",
        executor.get("email", ""),
        "",
        "---",
        "This letter was prepared by Aftercare, an automated assistant acting on the "
        "executor's instruction, and reviewed and approved by the executor before "
        "sending. It is not signed by, and makes no representation on behalf of, any "
        "party other than the executor named above.",
    ]

    reasoning = (
        f"Drafted from playbook '{playbook.get('name', 'generic')}' "
        f"v{playbook.get('version', '0.0.0')}. Enclosed {len(enclosures)} of "
        f"{len(enclosures) + len(withheld)} available fields; withheld {len(withheld)} as "
        f"not required by this recipient's process"
        + (f". Follow-up addressing {len(extra_requests)} outstanding request(s)." if is_followup else ".")
    )

    return {"subject": subject, "body": "\n".join(lines), "reasoning": reasoning}


# --- escalation.brief ----------------------------------------------------------------


def escalation_brief(inputs: dict[str, Any]) -> dict[str, Any]:
    """One paragraph, one recommendation, no decision.

    Boundary 4 says ambiguity escalates within one turn. Boundary 2 says the agent does
    not make the call. So the brief states the situation, the options, and what the agent
    would do - and stops.
    """
    institution = inputs.get("institution_name", "the institution")
    trigger = inputs.get("trigger", "an unanticipated response")
    detail = inputs.get("detail", "")
    options = inputs.get("options") or [
        "Provide what is being asked and continue",
        "Decline and ask the institution to justify the requirement",
        "Refer this one to the estate's attorney",
    ]
    category = inputs.get("category", "OTHER")

    brief = (
        f"{institution} has responded in a way the playbook did not anticipate: {trigger}. "
        + (f"Specifically: {detail} " if detail else "")
        + "This turns on a judgement I am not permitted to make on your behalf, because it "
        "concerns what the estate is willing to disclose or assert rather than a procedural "
        "step. Nothing has been sent and nothing will be until you decide. "
        f"Given the category ({category}) and the wording used, my recommendation would be "
        f"option 1, but the decision is yours."
    )
    return {
        "brief": brief,
        "options": options,
        "recommendation": options[0],
        "requires_human": True,
    }


# --- amendment.propose ---------------------------------------------------------------


def propose_amendment(inputs: dict[str, Any]) -> dict[str, Any]:
    """Turn a surprise into an asset.

    An institution asked for something the playbook did not list. Rather than handling it
    once and forgetting, the sub-agent proposes a new playbook version. Every future
    estate that touches this institution starts from the better version. This is the
    compounding-asset argument in ARCHITECTURE.md section 2.
    """
    current: str = inputs.get("current_version", "1.0.0")
    demanded: list[str] = inputs.get("demanded_documents", [])
    existing: list[str] = inputs.get("required_documents", [])
    institution: str = inputs.get("institution_name", "institution")

    new_items = [item for item in demanded if item not in existing]
    major, minor, patch = (list(map(int, current.split("."))) + [0, 0, 0])[:3]
    # Additive change to a required-document list is a minor bump: existing consumers of
    # the playbook stay correct, they just do less well.
    next_version = f"{major}.{minor + 1}.0" if new_items else f"{major}.{minor}.{patch + 1}"

    return {
        "proposed_version": next_version,
        "add_required_documents": new_items,
        "rationale": (
            f"{institution} required {', '.join(new_items) or 'no new documents'} that the "
            f"playbook at v{current} did not list. Adding {len(new_items)} item(s) as a "
            f"minor version bump so future estates enclose it on the first letter instead "
            f"of losing a round trip."
            if new_items
            else f"No new requirements observed; recording a patch against v{current}."
        ),
        "estimated_round_trips_saved": len(new_items),
    }


HANDLERS: dict[str, Any] = {
    "discovery.extract": extract_from_document,
    "discovery.infer": infer_hidden_obligations,
    "inbound.classify": classify_correspondence,
    "guardrail.judge": judge_injection,
    "packet.draft": draft_packet,
    "escalation.brief": escalation_brief,
    "amendment.propose": propose_amendment,
}
