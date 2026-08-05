import json
import boto3
import datetime
import os
import urllib
from engine import evaluate_all

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass          # platform sets env vars on Lambda

# ---- config: the script needs to know ----
# STATE_BUCKET = os.getenv("STATE_BUCKET", "drift-detector-tfstate-anushka")      # State Bucket
# STATE_KEY    = os.getenv("STATE_KEY", "drift-detector/terraform.tfstate")            # exact key to it
# MONITORED_BUCKET = os.getenv("MONITORED_BUCKET", "drift-detector-monitored-anushka")  
# RESOURCE_ID = os.getenv("RESOURCE_ID", "aws_s3_bucket_public_access_block.monitored")
TABLE_NAME  = os.getenv("TABLE_NAME", "drift-detector-history")
RENOTIFY_INTERVAL_SECONDS = int(os.getenv("RENOTIFY_INTERVAL_SECONDS", 120))
SNS_TOPIC_ARN= os.getenv("SNS_TOPIC_ARN",    "REDACTED")

sns = boto3.client("sns")
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

# the four attributes we monitor, by their state-file names
# PAB_KEYS = [
#     "block_public_acls",
#     "block_public_policy",
#     "ignore_public_acls",
#     "restrict_public_buckets",
# ]

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def post_to_slack(subject, message):
    payload = json.dumps({"text": f"*{subject}*\n{message}"}).encode("utf-8")
    req = urllib.request.Request(
        os.getenv("SLACK_WEBHOOK_URL"), data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"⚠️ Slack post failed: {e}")   # don't let Slack failure kill detection


def notify(subject, message):
    sns.publish(TopicArn=os.getenv("SNS_TOPIC_ARN"), Subject=subject, Message=message)
    post_to_slack(subject, message)
    print(f"\n📣 NOTIFY — {subject}\n   {message}")

def process(resource_id, resource_name, aspect, resource_type, drift):
    """Dedup state machine. MUST be called every run; drift may be empty."""
    table= dynamodb.Table(TABLE_NAME)
    rec = table.get_item(Key={"resource_id": resource_id}).get("Item")  # None if absent
    now = now_iso()

    current_names = sorted(name for name, _, _ in drift)
    current_attrs = [
        {"attribute": name, "declared": str(want), "actual": str(got)}
        for name, want, got in drift
    ]

    # ---------- no drift this run ----------
    if not drift:
        if rec and rec.get("status") == "FIRING":
            history = rec.get("history", [])
            history[-1]["resolved_at"] = now
            rec.update({"status": "RESOLVED", "current_attributes": [], "resolved_at": now, "history": history})
            table.put_item(Item=rec)
            notify("Drift RESOLVED", f"{resource_id} back in sync.")
            print("✅ Resolved — record updated.")
        else:
            print("✅ No drift.")
        return

    # ---------- drift this run ----------
    if rec is None:                                   # brand new
        table.put_item(Item={
            "resource_id": resource_id,
            "resource_name": resource_name,
            "resource_type": resource_type,
            "aspect": aspect,
            "status": "FIRING",
            "current_attributes": current_attrs,
            "history": [{"attributes": current_attrs, "detected_at": now, "last_seen": now}],
            "first_seen": now,
            "last_seen": now,
            "last_notified_at": now,
            "notification_count": 1,
            "total_notifications": 1,
        })
        notify("New drift detected", f"{resource_id}: {current_names}")
        print("🚨 New drift recorded + notified.")
        return

    # existing record: ongoing OR re-firing after resolve
    reopened = rec.get("status") == "RESOLVED"
    rec["status"] = "FIRING"
    rec["last_seen"] = now
    rec["current_attributes"] = current_attrs
    rec["resolved_at"] = None

    history = rec.get("history", [])
    last_names = sorted(a["attribute"] for a in history[-1]["attributes"]) if history else None
    if not reopened :
        if current_names != last_names:       # append-on-change (+ always on re-fire)
            history.append({"attributes": current_attrs, "detected_at": now, "last_seen": now})
        else:
            history[-1]["last_seen"] = now
    else:
        rec["first_seen"] = now
        rec["last_seen"] = now
        rec["resolved_at"] = None
        history.append({"attributes": current_attrs, "detected_at": now, "last_seen": now})

    rec["history"] = history

    should_notify = reopened
    new_notification=True
    if not reopened :
        if current_names == last_names :
            elapsed = (datetime.datetime.fromisoformat(now)
                    - datetime.datetime.fromisoformat(rec["last_notified_at"])).total_seconds()
            should_notify = elapsed >= RENOTIFY_INTERVAL_SECONDS
        else:
            should_notify = True
        new_notification = False

    if should_notify:
        rec["notification_count"] = int(rec.get("notification_count", 0)) + 1 if not new_notification else 1
        rec["total_notifications"] = int(rec.get("total_notifications", 0)) + 1 
        rec["last_notified_at"] = now
        table.put_item(Item=rec)
        tag = "re-fired" if reopened else "still firing"
        notify(f"Drift {tag}", f"{resource_id}: {current_names} (alert #{rec['notification_count']})")
        print(f"🚨 Drift {tag} — notified (count={rec['notification_count']}).")
    else:
        table.put_item(Item=rec)
        print("🔁 Ongoing — updated silently, no re-notify yet.")


# def get_desired_state():
#     """Read the four booleans Terraform recorded in the state file (S3)."""
#     obj = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
#     state = json.loads(obj["Body"].read())

#     for res in state["resources"]:
#         if res["type"] == "aws_s3_bucket_public_access_block" and res["name"] == "monitored":
#             attrs = res["instances"][0]["attributes"]
#             return {k: attrs[k] for k in PAB_KEYS}

#     raise RuntimeError("public_access_block resource not found in state file")


# def get_actual_state():
#     """Read the four booleans live AWS reports right now."""
#     resp = s3.get_public_access_block(Bucket=MONITORED_BUCKET)
#     cfg = resp["PublicAccessBlockConfiguration"]
#     # live API uses PascalCase; map back to the state-file snake_case names
#     mapping = {
#         "block_public_acls":       "BlockPublicAcls",
#         "block_public_policy":     "BlockPublicPolicy",
#         "ignore_public_acls":      "IgnorePublicAcls",
#         "restrict_public_buckets": "RestrictPublicBuckets",
#     }
#     return {snake: cfg[pascal] for snake, pascal in mapping.items()}


# def compare(desired, actual):
#     """Return a list of drifted attributes: (name, desired_value, actual_value)."""
#     drift = []
#     for key in PAB_KEYS:
#         if desired[key] != actual[key]:
#             drift.append((key, desired[key], actual[key]))
#     return drift


def run_detection():
    """The actual work — callable from both local main() and Lambda."""
    # desired = get_desired_state()
    # actual = get_actual_state()
    # print("Desired:", desired)
    # print("Actual :", actual)
    
    # process(compare(desired, actual))
    i=0
    se=set()
    for resource_id, resource_name, aspect, resource_type, drift in evaluate_all():
        process(resource_id, resource_name, aspect, resource_type, drift)
        i+=1
        se.add(resource_name)
        print("Iteration complete: Total resources aspects evaluated:", i)
        print("Unique resources evaluated:", len(se))


def lambda_handler(event, context):
    """Lambda entry point. EventBridge invokes this."""
    run_detection()
    return {"statusCode": 200, "body": "drift check complete"}


def main():
    run_detection()

if __name__ == "__main__":
    main()