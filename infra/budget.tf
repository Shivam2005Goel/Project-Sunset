# Cost control. README section 8.
#
# A full time-warp demo run costs roughly $0.40 in model calls, so a $25 alert is a very
# loud alarm rather than a routine notification: if this fires, something is looping.
#
# Skipped entirely when `billing_account` is unset, so `terraform apply` works for anyone
# who does not have billing-account-level permissions.

resource "google_billing_budget" "aftercare" {
  count = var.billing_account == "" ? 0 : 1

  billing_account = var.billing_account
  display_name    = "Aftercare demo budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}
