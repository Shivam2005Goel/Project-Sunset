# The fiduciary audit sink.
#
# Append-only by construction: the services stream rows in, and nothing in this codebase
# issues an UPDATE or a DELETE. Table deletion protection is on and rows never expire - an
# estate can be reopened long after the executor thought it was finished.

resource "google_bigquery_dataset" "audit" {
  dataset_id                 = "aftercare_audit"
  location                   = var.region
  description                = "Append-only record of every action taken on an estate's behalf, and the reasoning behind it."
  delete_contents_on_destroy = false

  default_table_expiration_ms = null # never expire; this is a legal record

  labels = {
    purpose = "fiduciary-audit"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "records" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "records"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "at"
  }

  clustering = ["estate_id", "institution_id", "action"]

  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED", description = "Record identifier" },
    { name = "seq", type = "INTEGER", mode = "REQUIRED", description = "Position in the hash chain" },
    { name = "at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "estate_id", type = "STRING", mode = "REQUIRED" },
    { name = "institution_id", type = "STRING", mode = "NULLABLE" },
    { name = "case_id", type = "STRING", mode = "NULLABLE" },
    { name = "actor", type = "STRING", mode = "REQUIRED", description = "Who acted: a service, or a named human" },
    { name = "action", type = "STRING", mode = "REQUIRED" },
    { name = "reasoning", type = "STRING", mode = "REQUIRED", description = "Why. Never empty - the state machine refuses transitions without one." },
    { name = "payload", type = "JSON", mode = "NULLABLE" },
    { name = "trace_id", type = "STRING", mode = "NULLABLE", description = "Joins this record to its Cloud Trace span" },
    { name = "prev_digest", type = "STRING", mode = "REQUIRED" },
    { name = "digest", type = "STRING", mode = "REQUIRED", description = "SHA-256 over this record and its predecessor's digest" },
  ])
}

# The view an executor's attorney would actually query: every outbound communication, who
# approved it, and exactly what was disclosed.
resource "google_bigquery_table" "outbound_view" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "outbound_communications"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = "SELECT `at`, estate_id, institution_id, JSON_VALUE(payload, '$.recipient') AS recipient, JSON_VALUE(payload, '$.approval_id') AS approval_id, JSON_VALUE(payload, '$.disclosed') AS disclosed_fields, JSON_VALUE(payload, '$.withheld') AS withheld_fields, reasoning, digest FROM `${var.project_id}.aftercare_audit.records` WHERE action = 'outbound.sent' ORDER BY `at`"
  }
}

# Every time the guardrail stopped something. Good in the demo, and the first thing a
# security reviewer asks for.
resource "google_bigquery_table" "blocked_view" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "blocked_inbound"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = "SELECT `at`, estate_id, institution_id, JSON_VALUE(payload, '$.message_id') AS message_id, reasoning, payload FROM `${var.project_id}.aftercare_audit.records` WHERE action = 'inbound.blocked' ORDER BY `at`"
  }
}

# Chain integrity as a query. If this returns any rows, the log has been tampered with.
resource "google_bigquery_table" "chain_breaks_view" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "chain_breaks"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = "SELECT seq, id, `at`, action, prev_digest, expected_prev FROM (SELECT seq, id, `at`, action, prev_digest, LAG(digest) OVER (ORDER BY seq) AS expected_prev FROM `${var.project_id}.aftercare_audit.records`) WHERE expected_prev IS NOT NULL AND prev_digest != expected_prev ORDER BY seq"
  }
}

output "audit_dataset" {
  value = google_bigquery_dataset.audit.dataset_id
}
