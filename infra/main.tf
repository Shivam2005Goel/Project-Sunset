# Aftercare infrastructure.
#
# "Reproducible setup" is 30% of the grade, so this is the artifact that has to work on a
# brand-new project with nothing configured. Day 10 in BUILD_PLAN.md exists to prove that:
# create an empty project, run `terraform apply`, run `make deploy`, run `make verify`.
#
# Never applied against a live project as of this commit - see the honesty note in
# CLAUDE.md.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "Target GCP project. Create it empty; this configuration fills it."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Must support Vertex AI. Agent Runtime availability is region-dependent - confirm on Day 0."
}

variable "budget_amount_usd" {
  type        = number
  default     = 25
  description = "Alert threshold. The whole demo runs well under the $150 credit grant."
}

variable "billing_account" {
  type        = string
  default     = ""
  description = "Required only for the budget alert in budget.tf."
}

locals {
  services = [
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "pubsub.googleapis.com",
    "cloudtasks.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "dlp.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "vision.googleapis.com",
  ]

  # One service account per agent role. See iam.tf for why this is not decoration.
  agent_roles = ["api", "orchestrator", "discovery", "inbox", "worker"]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.services)
  service  = each.value

  # Leave APIs enabled on destroy: disabling them can strand resources in other projects
  # that share the billing account, and re-enabling is slow.
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "aftercare"
  format        = "DOCKER"
  description   = "Aftercare service images"

  depends_on = [google_project_service.enabled]
}

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/aftercare"
}
