# Firestore holds the obligation graph, the per-institution state machines, the approval
# queue and the inbound message index.
#
# Native mode, not Datastore mode: the local JSON store mirrors the Native document model,
# and picking Datastore mode here would make the local implementation a misleading
# stand-in for the deployed one.

resource "google_firestore_database" "estate" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # An estate record is a legal artifact. Point-in-time recovery is cheap insurance
  # against the demo-day mistake of deleting the wrong collection.
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  delete_protection_state           = "DELETE_PROTECTION_DISABLED"

  depends_on = [google_project_service.enabled]
}

# The queries the services actually run. Firestore needs these declared, and finding that
# out at demo time is a bad way to find it out.
resource "google_firestore_index" "cases_by_estate_and_state" {
  project    = var.project_id
  database   = google_firestore_database.estate.name
  collection = "aftercare_cases"

  fields {
    field_path = "estate_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "state"
    order      = "ASCENDING"
  }
  fields {
    field_path = "institution_name"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "cases_due_to_wake" {
  project    = var.project_id
  database   = google_firestore_database.estate.name
  collection = "aftercare_cases"

  fields {
    field_path = "estate_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "next_wake_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "approvals_pending" {
  project    = var.project_id
  database   = google_firestore_database.estate.name
  collection = "aftercare_approvals"

  fields {
    field_path = "estate_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "inbound_by_estate" {
  project    = var.project_id
  database   = google_firestore_database.estate.name
  collection = "aftercare_inbound"

  fields {
    field_path = "estate_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "received_at"
    order      = "ASCENDING"
  }
}
