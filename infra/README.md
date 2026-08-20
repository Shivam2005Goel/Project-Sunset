# Infrastructure

```bash
cd infra
terraform init
terraform apply -var="project_id=$PROJECT_ID" -var="region=$REGION"
```

| File | What it provisions | Why it is worth reading |
|---|---|---|
| `main.tf` | APIs, Artifact Registry | The service list is the dependency list |
| `firestore.tf` | Native-mode database and its composite indexes | The indexes match the queries the services actually run |
| `pubsub.tf` | Three topics with dead-letter queues, Cloud Tasks queue | A dropped letter is a case that stalls silently for six weeks |
| `iam.tf` | One least-privilege service account per agent role | **The zero-trust story.** No agent holds a send permission |
| `storage.tf` | Uploads, artifacts, registry buckets | Source documents and generated artifacts have different lifecycles |
| `bigquery.tf` | Append-only audit dataset, partitioned, plus three views | The query surface an executor's attorney would use |
| `budget.tf` | A $25 alert | If this fires, something is looping |

## The three things a reviewer should check

1. **`iam.tf` grants nobody a Gmail send scope.** Outbound goes through the executor's own
   OAuth grant, held in Secret Manager and readable only by the API service - the one
   component that runs behind the approval gate.
2. **Institution sub-agents get `datastore.viewer` and `aiplatform.user`, nothing else.**
   A compromised utility agent cannot read the brokerage case file or write to the audit
   log.
3. **`bigquery.tf` has no update path.** The audit table is append-only by construction,
   deletion-protected, and `chain_breaks` is a view that returns rows only if someone
   rewrote history.

## Status

Written against the documented provider surface and **never applied to a live project**.
Day 10 in `BUILD_PLAN.md` is where that happens, against a brand-new project - a deploy
that only works from an already-configured laptop proves nothing about reproducibility,
and reproducibility is 30% of the grade.

Expect to spend that day on: API enablement ordering, Firestore location constraints
(the database location is not always the same string as the Cloud Run region), and the
Gmail push topic's IAM binding, which is the one non-obvious grant in the whole file.
