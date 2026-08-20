# Aftercare: An Autonomous Agent that Handles the Bureaucracy of Death

## Inspiration
When someone dies, their family inherits three to nine months of administrative work across 30–60 institutions—banks, insurers, pensions, utilities, registries. Each institution has its own unique forms, channels, and multi-week response times. Accounts left open accrue fees. Policies lapse unclaimed. Today, billions of dollars sit in state unclaimed-property databases because heirs simply never knew an account existed. All of this burden lands on a grieving executor who lacks a master list to work from.

We built Aftercare to lift this burden. 

## What it does
Aftercare is an autonomous estate-administration agent that discovers and manages bureaucratic obligations. 
- **Ingestion & Discovery:** The executor uploads a death certificate and statements. Aftercare’s Gemini multimodal discovery agent reconstructs the estate's obligation graph, even inferring hidden accounts through recurring debits.
- **Sub-Agent Fleet:** It runs a sleeping sub-agent per institution over multi-week timelines. These agents sleep between letters, holding zero CPU, and wake on inbound mail.
- **Guardrails & Safety:** All inbound correspondence is screened for prompt injection before hitting the agent's context. Outbound communications pass through a PII minimizer to enforce least-disclosure. 
- **The Human Boundary:** The agent never signs, asserts legal authority, or sends *anything* without explicit executor approval. The agent drafts; the human decides.
- **Audit Logging:** Because executors carry fiduciary duty, every state transition writes an append-only, hash-chained record to BigQuery, producing a court-defensible audit trail.

## How we built it
Aftercare leverages the Google Agent Development Kit (ADK) and Vertex AI (Gemini 3.5 Flash/Pro), built entirely on Google Cloud infrastructure.
- **Orchestration:** An ADK root orchestrator pulls versioned institution playbooks (YAML) from Agent Registry and drives an explicit finite state machine.
- **Long-Running Execution:** Sub-agents run on the Agent Runtime, utilizing Memory Bank for cross-session state. 
- **Async I/O:** Gmail watches push inbound mail to Pub/Sub. Model Armor screens it, and Gemini 3.5 Flash classifies it, moving the Estate FSM forward.
- **App & Dashboard:** The executor interfaces with the agent via a Next.js 15 dashboard running on Cloud Run, styled with Tailwind and shadcn/ui. 
- **IaC:** Zero-trust architecture provisioned with Terraform, utilizing one least-privilege service account per institution agent.

## Challenges we ran into
- **Long-running state over weeks:** Prompting a model to "remember where we are" creates a brittle script. We solved this by treating state as data, not vibes. We built a hard-coded Finite State Machine (FSM) that tracks each institution. 
- **Adversarial Inbound Mail:** Inbound documents (scanned letters, emails) are untrusted third-party inputs. A scanned letter with a prompt injection hidden in the OCR layer could hijack the agent. We overcame this by fencing and sanitizing all inputs with Model Armor before classification.
- **Hackathon Demo Constraints:** An agent designed to run for six weeks is hard to demo in four minutes. Instead of stubbing out the pipeline, we built a scripted time-warp driver. It injects a corpus of fictional inbound correspondence into the *real* Pub/Sub topic on an accelerated clock, proving our production code paths actually work.

## Accomplishments that we're proud of
1. **The Court-Defensible Audit Trail:** Instead of standard logging, we built an append-only, hash-chained audit sink into BigQuery. It produces an artifact the executor can hand to a probate court.
2. **The Fictional Demo Strategy:** It's incredibly easy to build a demo that exposes real people's PII. From Day 1, we seeded the system entirely with a fictional decedent and fabricated data, completely eliminating data privacy risks.
3. **Robust Security Boundaries:** Through structural design, we proved via static analysis that *no outbound path* can bypass the executor's approval queue. The safety boundary is enforced by architecture, not good intentions.

## What we learned
- **Agent Memory vs FSM:** The biggest learning was that long-term agent memory is better handled by traditional software patterns. When we forced the agent to deduce its current state by re-reading past context, it hallucinated steps. When we gave it a rigid FSM and only asked it to classify the next transition, its accuracy hit 100%.
- **Playbooks as compounding assets:** Treating institution processes as code (versioned YAML playbooks) means the agent fleet learns over time. When one sub-agent encounters an unanticipated demand, it proposes a playbook amendment. Every future estate benefits from that discovery.

## What's next for Aftercare
- **Direct Portal Integrations:** Expanding outbound capabilities from email/e-fax to direct portal form-fills via headless browser automation.
- **Expanded Registry Lookup:** Automating unclaimed-property lookups across all 50 states via unified data partnerships.
- **Legal Entity Spin-ups:** Assisting the executor in spinning up the Estate's EIN and Trust bank accounts programmatically.
