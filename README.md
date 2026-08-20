# Aftercare

**An autonomous agent that handles the bureaucracy of death.**

When someone dies, their family inherits three to nine months of administrative work
across 30-60 institutions - banks, insurers, pensions, utilities, carriers, registries -
each with its own forms, channels, and multi-week response times. Accounts left open
accrue fees. Policies lapse unclaimed. Billions sit in state unclaimed-property databases
because heirs never knew an account existed. All of it lands on a grieving executor who
has no list to work from.

Aftercare reconstructs that list, then runs a dedicated agent per institution for as long
as it takes - sleeping between letters, waking on inbound mail, adapting when an
institution demands something unexpected, and escalating to the human the moment
judgment is required.

> **Aftercare drafts; the executor decides.** The agent never signs, never asserts legal
> authority, never makes a legal determination, and never sends anything without explicit
> human approval. All demonstration data uses a fictional decedent and fabricated
> documents.

- **Track:** Fortified Enterprise Fleet
- **Architecture:** see [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- **Demo video:** _<link>_
- **Live deployment:** _<Cloud Run URL - may be scaled to zero; see §7 for proof of deployment>_

---

## 1. What it does

| Capability | Where |
|---|---|
| Reconstructs the estate's **obligation graph** from statements and mail photos | `services/discovery` |
| Publishes **versioned institution playbooks**, reusable across estates | `packages/playbooks` |
| Runs a **sleeping sub-agent per institution** over multi-week timelines | `services/worker` |
| Screens inbound correspondence for **prompt injection**; minimizes outbound **PII** | `packages/guardrails` |
| Emits a **hash-chained audit trail** of every action and its reasoning | `packages/core/audit` |
| Escalates ambiguity to the executor with a one-paragraph brief | `services/api` |

On the seeded estate: **23 obligations discovered, 4 of them from no document anyone
uploaded**, 19 closed, 2 escalated, 2 still in flight after six simulated weeks, 4
injection attempts blocked, and $11,563.40 recovered that the family would not have found.

---

## 2. Prerequisites

**For the local path, all you need is Python 3.11+.** Everything else is for the cloud
deployment.

| Requirement | Version | Needed for |
|---|---|---|
| Python | 3.11+ | everything |
| Node.js | 20+ | the dashboard |
| Google Cloud SDK | latest | cloud deploy |
| Terraform | 1.9+ | cloud deploy |
| Docker | 24+ | cloud deploy |
| `make` | any | optional - `python tasks.py <target>` does the same on Windows |

Run `python tasks.py doctor` to see what you have and what mode you are in.

---

## 3. Quickstart - local, no cloud, no API key (~2 minutes)

This path runs the full agent loop against a seeded fictional estate. It needs **no GCP
project, no API key and no network**. Use it to verify the project works before touching
anything else.

```bash
git clone https://github.com/<you>/aftercare.git
cd aftercare

python tasks.py setup      # pip install -e .[dev]  (+ npm install if node is present)
python tasks.py doctor     # confirms the toolchain and probes the model
python tasks.py seed       # builds the estate: discovery, fleet, 23 drafts
python tasks.py smoke      # end-to-end assertions
```

`smoke` ends with:

```
  SMOKE PASS - 23 discovered - 19 closed - 2 escalated - 2 pending
  26 checks - 4 injections blocked - $11,563.40 recovered - 281 audit records
```

**What is doing the reasoning?** With no key configured, `packages/core/offline.py` - a
deterministic rule-based planner that satisfies the same task contracts the Gemini
providers do. It is **not a model**, everything it produces is labelled
`offline-deterministic` wherever it surfaces, and the dashboard says so. The demo may run
without a model; it may never pretend to have used one.

To use a real model locally, set one variable:

```bash
cp .env.example .env       # then set GEMINI_API_KEY=<from https://aistudio.google.com/apikey>
```

Run the six-week replay and the dashboard:

```bash
python tasks.py demo       # six simulated weeks through the live pipeline
python tasks.py dev        # API on :8000
npm --prefix web run dev   # dashboard on :3000
```

---

## 4. Full deployment to Google Cloud (~20 minutes)

### 4.1 Project setup

```bash
export PROJECT_ID=aftercare-$(openssl rand -hex 3)
export REGION=us-central1

gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID
gcloud billing projects link $PROJECT_ID --billing-account=$BILLING_ACCOUNT_ID
```

Terraform enables the required APIs; you do not need to enable them by hand.

### 4.2 Secrets

```bash
gcloud secrets create gmail-oauth-client --data-file=./credentials/gmail_client.json
```

Nothing sensitive belongs in `.env` for cloud deploys - runtime secrets are read from
Secret Manager by the service accounts Terraform provisions.

### 4.3 Infrastructure

```bash
cd infra
terraform init
terraform apply -var="project_id=$PROJECT_ID" -var="region=$REGION"
cd ..
```

Provisions Firestore with its composite indexes, three Pub/Sub topics with dead-letter
queues, three GCS buckets, the append-only BigQuery audit dataset and its views, a Cloud
Tasks queue, Artifact Registry, and **one least-privilege service account per agent role**
(`infra/iam.tf` - this is the zero-trust story, keep it readable).

### 4.4 Deploy

```bash
export AFTERCARE_MODE=cloud
python tasks.py deploy              # builds and deploys 4 services + 1 job
python tasks.py publish-playbooks   # versioned playbooks to the registry
python tasks.py verify              # health, Firestore, one live Gemini call, a trace
```

> **If `seed` or `verify` fails with a 404 on the model**, the error names the fix. The
> most common cause is `AFTERCARE_MODE=cloud` left in a shell pointing at a project that
> cannot call the configured model. `python tasks.py doctor` prints where every value
> came from, and `AFTERCARE_MODE=local` gets you running again immediately.

---

## 5. Configuration reference

| Variable | Required | Default | Notes |
|---|---|---|---|
| `AFTERCARE_MODE` | no | `local` | `local` needs nothing else. `cloud` requires `PROJECT_ID`. |
| `GEMINI_API_KEY` | local only | - | Optional. Without it, the offline planner runs. |
| `PROJECT_ID` | cloud | - | |
| `REGION` | cloud | `us-central1` | Must support Vertex AI. |
| `MODEL_FAST` | no | see `packages/core/config.py` | Handles ~95% of calls. |
| `MODEL_DEEP` | no | see `packages/core/config.py` | Closure-packet synthesis only. |
| `RUNTIME_ADAPTER` | no | `agent_runtime` | or `cloud_run_jobs` |
| `MEMORY_ADAPTER` | no | `memory_bank` | or `firestore_vector` |
| `REGISTRY_ADAPTER` | no | `agent_registry` | or `gcs_versioned` |
| `GUARDRAIL_ADAPTER` | no | `model_armor` | or `dlp_plus_classifier` |
| `AUTO_SEND` | no | `false` | **Hard-wired false.** Present only to make the boundary explicit and testable. |
| `TIMEWARP_FACTOR` | no | `1` | `400` for the demo. Cosmetic - the driver advances the clock explicitly. |

`.env` is read at startup; **a variable already set in your shell wins over the file.**
In local mode every adapter resolves to its fallback regardless of what is configured, so
a local run cannot reach a Google endpoint by accident.

Model IDs are deliberately not hardcoded in this table - Day 0 in `BUILD_PLAN.md` exists
to confirm which model IDs your project can actually call, and the answer belongs in
`CLAUDE.md` and in `packages/core/config.py`, in one place each.

**Gmail scope:** Aftercare requests `gmail.readonly` on the *deceased's* mailbox and
`gmail.send` on the *executor's own* account. The agent never sends as the deceased.
Access is scoped, logged, and revocable.

---

## 6. Repository layout

```
aftercare/
├── infra/                    Terraform: IAM, Pub/Sub, Firestore, BigQuery, buckets
├── services/
│   ├── api/                  FastAPI - approval queue, audit export. transport.py is the only sender
│   ├── orchestrator/         ADK root agent + fleet planning
│   ├── discovery/            Cloud Run Job - obligation-graph reconstruction
│   ├── inbox/                Gmail watch -> screen -> classify -> FSM
│   └── worker/               Per-institution sub-agent
├── packages/
│   ├── core/                 Models, FSM, store, clock, LLM router, adapters/, audit/
│   ├── playbooks/            Institution playbooks (YAML) + registry publisher
│   └── guardrails/           Injection screening, PII minimizer, policy boundaries
├── web/                      Next.js dashboard
├── demo/                     Fictional estate, scripted corpus, time-warp driver
├── scripts/                  verify, deploy
└── tests/                    Unit, contract, adversarial, and the structural policy proof
```

---

## 7. Proof of Google Cloud deployment

The service is scaled to zero to conserve credits, so the URL may cold-start. Reproducible
evidence, all in-repo:

- `docs/proof/cloud-run-services.png` - Cloud Run console, deployed services
- `docs/proof/vertex-logs.png` - Vertex AI request logs
- `docs/proof/trace-waterfall.png` - Cloud Trace, one full six-week estate lifecycle
- `docs/proof/audit-bigquery.png` - BigQuery audit table with reasoning chains
- Demo video timestamps: console at **0:18**, live Cloud Run request at **2:41**

---

## 8. Cost control

Flash-first routing, `min-instances=0` on every service, `max-instances=3` caps,
512Mi/1vCPU baselines, and a $25 budget alert in `infra/budget.tf`. Tear down with
`python tasks.py destroy`. A full time-warp run costs roughly $0.40 in model calls, and
nothing at all in local mode.

---

## 9. Testing

```bash
python tasks.py test          # 330+ unit and contract tests
python tasks.py test-adv      # 40 injection payloads embedded in realistic letters
python tasks.py test-policy   # the structural safety proof
```

`test-policy` is the one to show a judge. It walks the AST of every file in the repo and
proves properties no prompt can undo:

- nothing outside `services/api/approvals.py` can reach the send path
- no route through the state machine reaches `SENT` without passing `AWAITING_APPROVAL`
- model and agent SDKs appear only in the adapter layer
- nothing reads wall-clock time outside `packages/core/clock.py`, so the time-warp is real
- raw inbound content is never interpolated into a prompt
- `AUTO_SEND` is a `False` literal and the environment cannot flip it

Each guard is also run against a temporary tree containing a **deliberately planted
violation**, so the tests are known to fail when they should. A guard nobody has watched
fail is not a guard.

---

## 10. Known limitations

Stated plainly, because the alternative is a judge finding them:

- **Nothing has been deployed to or verified against real GCP.** Every cloud adapter -
  Vertex, Firestore, Pub/Sub, BigQuery, DLP, Gmail, Agent Registry, Agent Runtime, Memory
  Bank, Model Armor - is written against the documented contract and **never executed
  against a live API**. Day 10 in `BUILD_PLAN.md` is where that gets tested.
- Playbooks cover 6 institutions in depth; the long tail falls back to a generic closure
  template plus human review, and the dashboard says which is which.
- Physical-mail ingestion is photo-upload only. In local mode the corpus ships with its
  text layer already extracted, because there is no vision model to run.
- Unclaimed-property lookup reads a fixture that models three state registries' response
  shape. Real registries expose an HTML form and no documented API;
  `services/discovery/unclaimed.py` is explicit about what is and is not claimed.
- The offline planner is rule-based, not a model. It is labelled everywhere.
- Not legal advice. Not a substitute for a probate attorney.

## License

MIT
