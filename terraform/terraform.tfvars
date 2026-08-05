
# Bucket A — where Terraform state lives. Must match the backend "s3" block.
state_bucket = "drift-detector-tfstate-anushka"

# Bucket B — the bucket you monitor for drift.
monitored_bucket = "drift-detector-monitored-anushka"

# Slack Incoming Webhook URL. Treat like a password — never commit.
slack_webhook_url = "REDACTED"

alert_email="doshi.an@northeastern.edu"

dashboard_ingress_cidr = "0.0.0.0/0"