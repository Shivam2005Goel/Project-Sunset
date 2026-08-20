# Proof of deployment

Screenshots go here on Day 10, captured against a **brand-new GCP project** rather than a
laptop that was already configured. README section 7 links to them by name:

| File | What it must show |
|---|---|
| `cloud-run-services.png` | The Cloud Run console listing the deployed services |
| `vertex-logs.png` | Vertex AI request logs for the model actually being called |
| `trace-waterfall.png` | Cloud Trace, one full estate lifecycle end to end |
| `audit-bigquery.png` | The BigQuery audit table with reasoning chains visible |

Capture the console loading live in the video rather than pasting these; the screenshots
are the in-repo record for a judge who does not watch to 2:41.

**Currently empty.** Nothing in this project has been deployed to or verified against real
GCP - see README section 10.
