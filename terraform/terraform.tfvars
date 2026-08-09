
# Bucket A — where Terraform state lives. Must match the backend "s3" block. REPLACE since s3 names are globally unique
state_bucket = "drift-detector-tfstate-anushka"
state_key= "drift-detector/terraform.tfstate"

# Bucket B — the bucket you monitor for drift. REPLACE since s3 names are globally unique
monitored_bucket = "drift-detector-monitored-anushka"

# Slack Incoming Webhook URL.
slack_webhook_url = "REDACTED"

#REPLACE with your email
alert_email="doshi.an@northeastern.edu"

dashboard_ingress_cidr = "0.0.0.0/0"