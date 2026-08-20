# Three buckets, deliberately separate.
#
# Uploads hold the executor's source documents and quarantined inbound mail: content that
# must be retained and never rewritten. Artifacts hold generated packets and exports,
# which are reproducible. Registry holds published playbook versions. Mixing them would
# mean one lifecycle policy for three very different risks.

resource "google_storage_bucket" "uploads" {
  name          = "${var.project_id}-aftercare-uploads"
  location      = var.region
  force_destroy = false # an estate's source documents are not disposable

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "artifacts" {
  name          = "${var.project_id}-aftercare-artifacts"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.enabled]
}

# The registry fallback. Object versioning is what makes "a published version is
# immutable" true at the storage layer, not only in application code.
resource "google_storage_bucket" "registry" {
  name          = "${var.project_id}-aftercare-registry"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.enabled]
}

output "buckets" {
  value = {
    uploads   = google_storage_bucket.uploads.name
    artifacts = google_storage_bucket.artifacts.name
    registry  = google_storage_bucket.registry.name
  }
}
