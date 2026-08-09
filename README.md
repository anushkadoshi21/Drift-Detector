# Drift Detector

Continuous, automated detection of **infrastructure drift** — the gap between what
your Terraform *declares* and what your AWS account *actually looks like* right now.

`terraform plan` already detects drift, but only when a human runs it. Real
infrastructure drifts silently between those manual checks: someone opens a
security-group port in the console, an automated process flips a setting, a
resource is modified by hand. Drift Detector runs on a schedule, catches these
out-of-band changes within minutes, records them, and alerts — no human in the loop.

> **What it compares:** Terraform **state** (what Terraform *believes* is deployed)
> against **live AWS** (what the APIs report now) — not the `.tf` source against
> reality. This is the `terraform plan -refresh-only` notion of drift.

> **A note on AI assistance.** This README, along with many of the inline code
> comments, some code for dashboard UI was drafted with the help of an AI assistant, and AI was used as a
> thinking partner while working through the architecture and debugging. All design
> decisions, the implementation, and the choices about what to build (and what to
> scope out) are the author's own. The AI-assisted documentation is intended to make
> the project easier to follow and reproduce.

---

## How it works

```
EventBridge (every 5 min)
      │
      ▼
   Lambda ──── reads Terraform state ────▶ S3 (tfstate)
      │   ──── queries live AWS ─────────▶ boto3 describe/get APIs
      │
      ├── writes drift history ──────────▶ DynamoDB
      └── sends alerts ──────────────────▶ SNS (email) + Slack (webhook)

   Dashboard (Streamlit on ECS Fargate) ── reads ──▶ DynamoDB
```

1. **EventBridge** triggers the **Lambda** on a schedule.
2. The Lambda reads the **Terraform state file** from **S3** (declared state) and
   queries **live AWS** via boto3 (actual state).
3. It diffs them per resource. Drift is written to **DynamoDB** as a per-resource
   lifecycle record, and alerts fire via **SNS** email and a **Slack** webhook.
4. A **Streamlit dashboard** (containerized, on **ECS Fargate**) reads DynamoDB and
   shows what's drifting now, what's resolved, and each resource's timeline.

---

## Architecture: config for the common case, code for the structural case

The engine is **config-driven**. A YAML file declares *what* to monitor; a dynamic
engine builds the right boto3 client, calls the right API, and diffs generically.
Custom Python is written **only** when a comparison is genuinely structural.

| Mismatch between state and live | How it's handled |
|---|---|
| **Name casing** — `block_public_acls` vs `BlockPublicAcls` | Automatic snake→Pascal, with per-attribute overrides |
| **Value location** — nested `versioning_configuration[0].status`, `Reservations[0].Instances[0]` | Config-declared dotted path (`dig`) |
| **Structural** — security-group rules as unordered lists of objects | A per-type Python **handler** |

**Consequences of this design:**

- Add an **attribute** to an existing type → one line of YAML, no code.
- Add a **scalar resource type** (e.g. EC2 instance type, S3 versioning) → a config
  block, no comparison code.
- Add a **structural type** (e.g. security groups) → one small handler, isolated.
- Resources are **discovered from the Terraform state file** — nothing is hardcoded,
  so a resource added in Terraform is monitored automatically.

---

## Monitored resource types

| Resource type | Aspect | Comparison |
|---|---|---|
| S3 bucket | Public access block (4 booleans) | Config (scalar) |
| S3 bucket | Versioning status | Config (nested scalar) |
| EC2 instance | Instance type | Config (nested scalar) |
| Security group | Ingress / egress rules (IPv4 CIDR) | Python handler (structural) |

---

## Drift lifecycle & deduplication

Each monitored resource is one DynamoDB record with a **FIRING → RESOLVED**
lifecycle rather than a stream of duplicate rows:

- **New drift** → record created, alert sent.
- **Ongoing drift** → record updated every run silently; re-alerts at most once per
  `RENOTIFY_INTERVAL_SECONDS` (default 24h), so a persistent drift doesn't spam.
- **Resolved** → status flips to RESOLVED; the record and its history persist.
- **History** appends an entry whenever the drifted attribute set changes.

---

## Repository layout

```
.
├── terraform/            # all infrastructure as code
│   ├── main.tf           # state backend, monitored resources, Lambda, DynamoDB, SNS
│   ├── ecs.tf            # dashboard hosting (ECS Fargate, ECR, roles, SG)
│   └── terraform.tfvars  # secrets & config (gitignored)
├── detector/             # the detection engine (runs as the Lambda)
│   ├── detect.py         # entry point + dedup state machine
│   ├── engine.py         # config-driven fetch + compare
│   ├── handlers.py       # per-type handlers (security groups)
│   └── config.yaml       # what to monitor
├── dashboard/            # Streamlit dashboard (containerized)
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
└── README.md
```

---

## Setup

### Prerequisites

- AWS account with credentials configured (`aws configure`)
- Terraform ≥ 1.10
- Python ≥ 3.9
- Docker (for the dashboard container)

### 1. Bootstrap the state backend

The Terraform state bucket must exist before Terraform can use it as a backend
(chicken-and-egg — it's the one resource created by hand):

```bash
aws s3api create-bucket --bucket <your-tfstate-bucket> --region us-east-1
aws s3api put-bucket-versioning --bucket <your-tfstate-bucket> \
  --versioning-configuration Status=Enabled
```

### 2. Configure

Create `terraform/terraform.tfvars` (gitignored — never commit):

```hcl
state_bucket           = "<your-tfstate-bucket>"
state_key              = "drift-detector/terraform.tfstate"  # MUST match the backend `key` below
monitored_bucket       = "<your-monitored-bucket>"
alert_email            = "you@example.com"
slack_webhook_url      = "https://hooks.slack.com/services/..."
dashboard_ingress_cidr = "<your-ip>/32"   # or 0.0.0.0/0 to open (not recommended)
```

> **⚠️ Also edit the backend block by hand.** Terraform **cannot** use variables in
> a `backend` block (it reads the backend before variables exist), so the state
> bucket name is **hardcoded** in `terraform/main.tf`:
>
> ```hcl
> backend "s3" {
>   bucket = "drift-detector-tfstate-anushka"   # <-- change to YOUR state bucket
>   key    = "drift-detector/terraform.tfstate"
>   region = "us-east-1"
>   ...
> }
> ```
>
> Setting `state_bucket` in `terraform.tfvars` is **not** enough — the tfvars value
> only feeds the IAM policy that grants the Lambda read access. The `backend` block
> is a separate, hardcoded copy. **Both must name the same bucket**, or `terraform
> init` targets the wrong backend (or fails). Update the hardcoded `bucket` (and
> `region`, if you changed it) to match your `state_bucket` before `terraform init`.
>
> The same applies to the state **`key`**: `state_key` in tfvars (which the
> detector uses to find the state file) **must** equal the hardcoded `key` in the
> backend block. If they differ, the Lambda reads the wrong path and finds no
> state.

### 3. Build the Lambda dependency layer

The Lambda bundles `detect.py`, `engine.py`, `handlers.py`, and `config.yaml`, plus a
**PyYAML layer** (PyYAML is not in the Lambda runtime). The layer's contents are
**gitignored** (installed third-party files don't belong in the repo), so they must
be **regenerated on every fresh clone** before Terraform can zip them — do this
**before** the next step.

Run the build script from the repo root:

```bash
bash scripts/build_layer.sh
```

This installs PyYAML into `layer/python/` built for **Lambda's platform**
(`linux/x86_64`, Python 3.12) — not your local machine's. Skipping it makes the
next step fail with `layer/python not found`.

> The script pins `--platform manylinux2014_x86_64` and `--python-version 3.12`
> on purpose: building on Apple Silicon / a newer local Python otherwise produces a
> layer that imports fine locally but fails at runtime on Lambda.

### 4. Initialize Terraform

```bash
cd terraform
terraform init
```

> **Note on apply order.** Do **not** run a single `terraform apply` yet. The ECS
> dashboard service pulls an image from ECR that doesn't exist until it's built and
> pushed — so a full apply would fail creating the service. The deploy is therefore
> ordered: create the ECR repo → push the image → apply the rest. This is handled in
> **step 5**. (If you are *only* deploying the detector and not the dashboard, you
> can run `terraform apply` now — the detector stack has no image dependency.)

After the detector is deployed (step 5), confirm the SNS email subscription (check
your inbox and click the link) — the subscription is created but delivers nothing
until confirmed.

### 5. Run the dashboard

**Locally (simplest):**

```bash
cd dashboard
pip install -r requirements.txt
streamlit run app.py
```

**On ECS Fargate** — there is a chicken-and-egg here: the ECS service pulls an
image from ECR, but the image can't be pushed until the ECR repository exists, and
the repository is created *by Terraform*. So the deploy is ordered in three steps
(you cannot push "before" apply — the repo isn't there yet):

**Step 1 — create just the ECR repository:**

```bash
cd terraform
terraform apply -target=aws_ecr_repository.dashboard
terraform output ecr_repo_url            # copy this
```

**Step 2 — build the image (for Fargate's platform) and push it:**

```bash
cd ../dashboard
ECR_URL=$(cd ../terraform && terraform output -raw ecr_repo_url)

# authenticate Docker to ECR (token is valid ~12h)
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "$ECR_URL"

# build for linux/amd64 — an arm64 image (default on Apple Silicon) fails on Fargate
docker build --platform linux/amd64 -t "$ECR_URL:latest" .
docker push "$ECR_URL:latest"
```

**Step 3 — apply everything else now that the image exists:**

```bash
cd ../terraform
terraform apply
```

This second apply brings up the full detector stack (Lambda, DynamoDB, SNS,
EventBridge) **and** the ECS cluster/task/service together — the service can now
pull its image successfully.

> **Why `-target` in step 1?** Without it, a single `terraform apply` would try to
> create the ECS service in the same pass — and it would fail with
> `CannotPullContainerError`, because no image is in ECR yet. Targeting the repo
> first lets us push an image before the service tries to pull it. `-target` is a
> deliberate one-time bootstrap step here.

**Whenever you change the dashboard afterward**, rebuild + repush (step 2), then
force a new deployment so ECS pulls the new image:

```bash
aws ecs update-service --cluster drift-detector-cluster \
  --service drift-dashboard --force-new-deployment --region us-east-1
```

**Finding the dashboard URL:** the Fargate task gets a *public IP that changes on
every restart*. Get the current one from the ECS console (Cluster → Service → task
→ Public IP) or via the CLI, then open `http://<public-ip>:8501`.

> **Production note — this manual push is a stopgap.** In a real deployment,
> Terraform manages *infrastructure* and a *CI/CD pipeline* manages *images* — the
> two are kept separate. A production setup would move image builds into CI/CD (see Roadmap).

---

## Configuration reference (`detector/config.yaml`)

```yaml
resource_types:
  s3_public_access_block:
    resource_type: S3 Bucket            # display grouping
    state_type: aws_s3_bucket_public_access_block  # how instances are found in state
    api: get_public_access_block        # boto3 method
    parameter: Bucket                   # the API's argument name
    response_key: PublicAccessBlockConfiguration
    id_attribute: bucket
    attributes:                         # auto snake→Pascal unless overridden
      - block_public_acls
      - block_public_policy

  security_group:
    resource_type: Security Group
    state_type: aws_security_group
    id_attribute: id
    handler: security_group             # routes to a Python handler instead

clients:
  S3 Bucket:  { client: client, service: s3 }
  Security Group: { client: client, service: ec2 }
```

Add a new **attribute**: append to `attributes`. Add a **scalar type**: add a block
with `state_type` / `api` / `parameter` / `attributes`. Add a **structural type**:
write a handler in `handlers.py` and reference it with `handler:`.

---

## Local development

```bash
cd detector
pip install boto3 pyyaml
python3 detect.py          # runs the full detection loop against real AWS
```

Test the full lifecycle by inducing drift out-of-band, then re-running:

```bash
# induce
aws s3api put-public-access-block --bucket <monitored-bucket> \
  --public-access-block-configuration BlockPublicAcls=false,BlockPublicPolicy=true,IgnorePublicAcls=true,RestrictPublicBuckets=true
python3 detect.py          # expect drift detected
# revert
aws s3api put-public-access-block --bucket <monitored-bucket> \
  --public-access-block-configuration BlockPublicAcls=true,BlockPublicPolicy=true,IgnorePublicAcls=true,RestrictPublicBuckets=true
```

---

## Scope & limitations

This is an MVP. Deliberately out of scope:

- **Unmanaged resources** — only drift on Terraform-managed resources is detected,
  not resources created entirely outside Terraform (the harder problem tools like
  driftctl solve).
- **Security groups** — IPv4 CIDR rules only; SG-to-SG references, IPv6, and
  all-traffic (`-1`) rules are out of scope.
- **Attribution** — the system detects *what* drifted, not *who* changed it
  (would require CloudTrail integration).
- **Dashboard** — served over HTTP with a public IP that changes on task restart;
  production would use a load balancer, HTTPS, and authentication.

## Roadmap

- Detect unmanaged / rogue resources
- CloudTrail integration for "who changed this"
- More resource types (RDS, IAM roles) and richer rule comparison
- CI/CD image builds (GitHub Actions → ECR, OIDC auth, SHA-tagged images) so deploys don't depend on a local machine
- Production hardening: stable dashboard URL behind a load balancer + HTTPS + auth, and secrets in Secrets Manager

---

## Teardown

```bash
cd terraform
terraform destroy
```

Note the Terraform state bucket was created by hand, so remove it separately if
desired.
