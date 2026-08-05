# ---------- Block 1: Terraform settings + backend ----------
terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket       = "drift-detector-tfstate-anushka"   # Terraform state bucket
    key          = "drift-detector/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

variable "alert_email" {
  description = "Email address to receive drift alerts"
  type        = string
}

variable "state_bucket"      { type = string }
variable "monitored_bucket"  { type = string }
variable "slack_webhook_url" {
  type      = string
  sensitive = true
}

# ---------- Block 2: Provider ----------
provider "aws" {
  region = "us-east-1"
}

# ---------- Block 3: The monitored bucket ----------
resource "aws_s3_bucket" "monitored" {
  bucket = "drift-detector-monitored-anushka"   
}

# ---------- Block 4: The public access block actually being monitor ----------
resource "aws_s3_bucket_public_access_block" "monitored" {
  bucket = aws_s3_bucket.monitored.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

#-----------------Monitor S3 Versioning--------------
resource "aws_s3_bucket_versioning" "monitored" {
  bucket = aws_s3_bucket.monitored.id
  versioning_configuration {
    status = "Enabled"
  }
}


#Latest Amazon Linux 2023 AMI at apply time 
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}


#-------------- Monitor EC2-----------------
resource "aws_instance" "monitored" {
  ami           = data.aws_ami.al2023.id
  instance_type = "t3.micro"          # the attribute we monitor
  tags = {
    Name = "drift-detector-monitored-ec2"
  }
}

#-------------Monitor Security Group------------
resource "aws_security_group" "monitored" {
  name        = "drift-detector-monitored-sg"
  description = "SG monitored for drift"

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


# ---------- Block 5: DynamoDB table for drift history ----------
resource "aws_dynamodb_table" "drift_history" {
  name         = "drift-detector-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key  = "resource_id"       #resource_name:aspect
  attribute {
    name = "resource_id"
    type = "S"
  }
}


# ---------- Block 6: SNS topic + email subscription ----------
resource "aws_sns_topic" "drift_alerts" {
  name = "drift-detector-alerts"
}


resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.drift_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

output "sns_topic_arn" {
  value = aws_sns_topic.drift_alerts.arn
}

# ---------- Layer: python dependencies (PyYAML) ----------
data "archive_file" "deps_layer" {
  type        = "zip"
  source_dir  = "${path.module}/../layer"          # contains python/ with pyyaml
  output_path = "${path.module}/deps_layer.zip"
}

resource "aws_lambda_layer_version" "deps" {
  layer_name          = "drift-detector-deps"
  filename            = data.archive_file.deps_layer.output_path
  source_code_hash    = data.archive_file.deps_layer.output_base64sha256
  compatible_runtimes = ["python3.12"]
}


# ---------- Block 7: Package the code ----------
data "archive_file" "detector_zip" {
  type        = "zip"
  source_dir = "${path.module}/../detector"
  output_path = "${path.module}/detector.zip"
  excludes    = ["__pycache__", "*.pyc", ".env"]
}

# ---------- Block 8: IAM role — the trust policy (Assumerole to attach permissions) ----------
resource "aws_iam_role" "detector" {
  name = "drift-detector-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ---------- Block 9: Permissions policy (WHAT it can do) ----------
resource "aws_iam_role_policy" "detector" {
  name = "drift-detector-permissions"
  role = aws_iam_role.detector.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadStateFile"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.state_bucket}/*"
      },
      {
        Sid      = "ReadMonitoredBucketPAB"
        Effect   = "Allow"
        Action   = ["s3:GetBucketPublicAccessBlock"]
        Resource = "arn:aws:s3:::${var.monitored_bucket}"
      },
      {
        Sid      = "ReadBucketVersioning"
        Effect   = "Allow"
        Action   = ["s3:GetBucketVersioning"]
        Resource = "arn:aws:s3:::${var.monitored_bucket}"
      },
      {
        Sid      = "DescribeEC2"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"          # see the flag below — EC2 describe can't be resource-scoped
      },
      {
        Sid      = "DescribeSecurityGroups"
        Effect   = "Allow"
        Action   = ["ec2:DescribeSecurityGroups"]
        Resource = "*"          # describe actions can't be resource-scoped (same as DescribeInstances)
      },
      {
        Sid      = "WriteDriftHistory"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.drift_history.arn
      },
      {
        Sid      = "PublishAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.drift_alerts.arn
      },
      {
        Sid      = "WriteLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# ---------- Block 10: The Lambda function ----------
resource "aws_lambda_function" "detector" {
  function_name    = "drift-detector"
  role             = aws_iam_role.detector.arn
  handler          = "detect.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30

  filename         = data.archive_file.detector_zip.output_path
  source_code_hash = data.archive_file.detector_zip.output_base64sha256
  layers = [aws_lambda_layer_version.deps.arn]   

  environment {
    variables = {
      STATE_BUCKET              = var.state_bucket
      STATE_KEY                 = "drift-detector/terraform.tfstate"
      MONITORED_BUCKET          = var.monitored_bucket
      TABLE_NAME                = aws_dynamodb_table.drift_history.name
      SNS_TOPIC_ARN             = aws_sns_topic.drift_alerts.arn
      SLACK_WEBHOOK_URL         = var.slack_webhook_url
      RENOTIFY_INTERVAL_SECONDS = "120"
    }
  }
}

# ---------- Block 11: EventBridge rule — the schedule ----------
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "drift-detector-schedule"
  schedule_expression = "rate(2 minutes)"
}

# ---------- Block 12: Point the rule at the Lambda ----------
resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.schedule.name
  arn  = aws_lambda_function.detector.arn
}

# ---------- Block 13: Let EventBridge invoke the Lambda (the forgotten piece) ----------
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.detector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}


resource "aws_ecr_repository" "dashboard" {
  name                 = "drift-dashboard"
  image_tag_mutability = "MUTABLE"
  force_delete         = true        
}

output "ecr_repo_url" {
  value = aws_ecr_repository.dashboard.repository_url
}