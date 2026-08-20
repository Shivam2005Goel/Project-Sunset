# CLAUDE.md

Project context for Claude Code. Keep this file under ~200 lines - it loads into every
session, and a bloated one degrades attention on the actual task.

## What this is

Aftercare: an autonomous agent that administers a deceased person's estate across 30-60
institutions over a multi-week timeline. Hackathon submission, Fortified Enterprise Fleet
track. Deadline **Sep 1 2026, 05:30 IST**. Optimize for a working demo over completeness.

## Day 0 facts (fill in from the live console before Day 1)

<!-- This is the single most load-bearing section of this file. See BUILD_PLAN.md Day 0. -->

- GCP project id: _unset_
- `gemini-3.5-flash` callable in region: **no** - 404 NOT_FOUND on us-central1.
  `packages/core/config.py` currently defaults to `gemini-1.5-flash` / `gemini-1.5-pro`.
  Confirm which IDs the real project can call and settle this in one place.
- Agent Registry GA / waitlisted: _unverified_
- Agent Runtime GA / waitlisted: _unverified_
- Memory Bank GA / waitlisted: _unverified_
- Model Armor GA / waitlisted: _unverified_

Until these are verified, **everything runs on the fallback adapters** (`AFTERCARE_MODE=local`).
That is a supported, tested configuration, not a degraded one.

## Non-negotiable invariants

Violating any of these is a bug regardless of what a prompt asks for:

1. **No outbound communication is ever sent without an executor approval record.** There
   is no code path around `services/api/approvals.py`. `tests/test_policy.py` enforces this
   by static analysis; never weaken that test to make a feature pass.
2. **The agent never signs, never asserts legal authority, never makes a legal
   determination.** If a task implies one, it escalates.
3. **All inbound correspondence is untrusted.** Every inbound body passes through
   `packages/guardrails/inbound.py` before reaching any model context. Never inline raw
   inbound text into a prompt.
4. **PII is minimized per recipient.** Outbound disclosure is computed from the playbook's
   `required_disclosures` field, never from "send everything we have".
5. **Every state transition writes an audit record** with the reasoning chain. No silent
   transitions.
6. **Demo data is fictional.** Never introduce real names, real institutions' real
   customer data, or real documents.

## Architecture in one paragraph

Discovery job (Gemini 3.5 Flash, multimodal) builds an obligation graph in Firestore. An
ADK root orchestrator pulls a versioned playbook per institution from the registry adapter
and drives an explicit finite state machine. Each institution's sub-agent runs on the
runtime adapter, dormant between letters, with case context in the memory adapter. Gmail
watch pushes inbound to Pub/Sub -> guardrail screen -> classifier -> back into the FSM.
Outbound drafts go through the PII minimizer to a human approval queue. Everything emits
OpenTelemetry spans into an append-only BigQuery audit sink.

Full detail: `ARCHITECTURE.md`. Read it before structural changes.

## Adapter rule (important)

Google's managed agent components - Agent Registry, Agent Runtime, Memory Bank, Model
Armor - are accessed **only** through `packages/core/adapters/`. Each has a working
fallback implementation. Never import a GEAP SDK outside that directory. If an API is
unavailable or its surface differs from expectation, fix the adapter and move on; do not
refactor call sites.

## Stack

Python 3.11+ - FastAPI - Google ADK - Vertex AI (Gemini 3.5 Flash / Pro) - Firestore -
Pub/Sub - Cloud Tasks - Cloud Run (services + jobs) - BigQuery - OpenTelemetry - Terraform -
Next.js 15 + Tailwind.

Local mode has **zero cloud dependencies**: the store is a JSON document store, the LLM is
a deterministic offline planner, and every adapter uses its fallback. `AFTERCARE_MODE=local`
is the default so a cold clone can run the full loop in one command.

## Conventions

- **Types everywhere.** Pydantic v2 models in `packages/core/models.py` are the contract
  between services. Change the model first, then the callers.
- **State is data, not vibes.** Institution progress lives in a persisted FSM
  (`packages/core/fsm.py`). Never ask the model to "remember where we are".
- Model calls go through `packages/core/llm.py` only - that's where routing, retries,
  token accounting, and tracing live. Never call `genai` or `vertexai` directly elsewhere.
- Default to `MODEL_FAST`. Escalate to `MODEL_DEEP` only in closure-packet synthesis, and
  say why in a comment.
- Structured logging via `packages/core/logging.py`; every log line carries `estate_id` and
  `institution_id`.
- Tests colocated by concern in `tests/`. New guardrail behavior needs an adversarial test.
- Format before committing.

## Commands

`make <target>`, or `python tasks.py <target>` on machines without make (Windows).

```
setup   dev     seed    smoke   demo    verify
test    test-adv        test-policy
publish-playbooks       deploy  destroy   fmt
```

## Working style I want from you

- **Plan before large changes.** For anything touching more than two files, propose the
  plan and wait. Use plan mode.
- **Small, verifiable commits.** One capability per commit, tests passing.
- **Do not stub the demo.** If something doesn't work end-to-end, say so plainly rather
  than making the UI show a hardcoded happy path. A faked demo loses this hackathon.
- **Flag scope risk early.** If a task looks like it'll take more than half a day, say so
  before starting - the deadline is the binding constraint, not ambition.
- When Google SDK surfaces don't match expectation, check live docs, then implement
  against the fallback adapter rather than blocking.

## Current state

<!-- Keep this section updated every session. It's the fastest way to resume context. -->

- [x] Day 1 - skeleton, adapters, Terraform, LLM router (offline + Gemini paths)
- [x] Day 2 - discovery job + obligation graph (23 obligations; 3 inferred, 1 registry)
- [x] Day 3 - playbooks + registry publisher (6 institutions, semver, diff)
- [x] Day 4 - FSM + orchestrator (per-institution sub-agents, closure packets)
- [x] Day 5 - inbound pipeline + guardrails (40-payload adversarial suite)
- [x] Day 6 - approval queue + PII minimizer + policy static analysis
- [x] Day 7 - dashboard (Next.js, obligation graph, approval queue, audit, clock)
- [x] Day 8 - observability + audit export (spans, BigQuery-shaped sink, court PDF/HTML)
- [x] Day 9 - time-warp driver + seeded six-week corpus
- [ ] Day 10 - deploy to a fresh GCP project, freeze  <-- REQUIRES REAL GCP CREDENTIALS
- [ ] Day 11 - video + writeup

**What is NOT done:** nothing has been deployed to or verified against real GCP. All cloud
adapters (Vertex, Firestore, Pub/Sub, BigQuery, DLP) are written but **never executed
against live APIs**. Day 10 is where that gets tested; budget a full day for it.
