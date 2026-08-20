# Eventing. Three topics, each with a dead-letter queue, because a dropped inbound letter
# is a case that silently stalls for six weeks and an executor who never finds out.

resource "google_pubsub_topic" "inbound" {
  name       = "aftercare-inbound"
  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "agent_events" {
  name       = "aftercare-agent-events"
  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "audit" {
  name       = "aftercare-audit"
  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "dead_letter" {
  name       = "aftercare-dead-letter"
  depends_on = [google_project_service.enabled]
}

# Gmail's push notifications are published by a Google-owned service account, which needs
# publish rights on the topic it targets.
resource "google_pubsub_topic_iam_member" "gmail_publisher" {
  topic  = google_pubsub_topic.inbound.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

resource "google_pubsub_subscription" "inbound_push" {
  name  = "aftercare-inbound-push"
  topic = google_pubsub_topic.inbound.name

  push_config {
    push_endpoint = "https://aftercare-inbox-${var.project_id}.run.app/pubsub/push"
    oidc_token {
      service_account_email = google_service_account.agents["inbox"].email
    }
  }

  # An inbound letter can take a moment to OCR and screen. Ten seconds is not enough and
  # produces duplicate deliveries.
  ack_deadline_seconds       = 60
  message_retention_duration = "604800s" # seven days

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_subscription" "dead_letter_hold" {
  name                       = "aftercare-dead-letter-hold"
  topic                      = google_pubsub_topic.dead_letter.name
  message_retention_duration = "2592000s" # thirty days - long enough to notice and replay
}

resource "google_cloud_tasks_queue" "wakeups" {
  name     = "aftercare-wakeups"
  location = var.region

  rate_limits {
    max_dispatches_per_second = 10
    max_concurrent_dispatches = 20
  }

  retry_config {
    max_attempts  = 5
    min_backoff   = "30s"
    max_backoff   = "3600s"
    max_doublings = 4
  }

  depends_on = [google_project_service.enabled]
}
