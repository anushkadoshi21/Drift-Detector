# ---------- Default VPC + subnets ----------
data "aws_vpc" "default" {
  default = true
}
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ---------- CloudWatch log group ----------
resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/ecs/drift-dashboard"
  retention_in_days = 7
}

# ---------- Security group: allow dashboard port ----------
resource "aws_security_group" "dashboard" {
  name        = "drift-dashboard-sg"
  description = "Dashboard ingress on 8501"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Streamlit"
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = [var.dashboard_ingress_cidr]   
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]                  # container needs outbound 
  }
}

variable "dashboard_ingress_cidr" {
  description = "CIDR allowed to reach the dashboard."
  type        = string
}

# ---------- Role 1: Execution role (ECS agent pulls image, writes logs) ----------
resource "aws_iam_role" "ecs_execution" {
  name = "drift-dashboard-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ---------- Role 2: TASK role (your CODE reads DynamoDB) ----------
resource "aws_iam_role" "ecs_task" {
  name = "drift-dashboard-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action = "sts:AssumeRole"
    }]
  })
}
resource "aws_iam_role_policy" "ecs_task_dynamo" {
  name = "dashboard-read-dynamo"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem"]
      Resource = aws_dynamodb_table.drift_history.arn
    }]
  })
}

# ---------- ECS cluster ----------
resource "aws_ecs_cluster" "main" {
  name = "drift-detector-cluster"
}

# ---------- Task definition ----------
resource "aws_ecs_task_definition" "dashboard" {
  family                   = "drift-dashboard"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "dashboard"
    image     = "${aws_ecr_repository.dashboard.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8501, protocol = "tcp" }]
    environment = [
      { name = "AWS_DEFAULT_REGION", value = "us-east-1" },
      { name = "TABLE_NAME",         value = aws_dynamodb_table.drift_history.name }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.dashboard.name
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "dashboard"
      }
    }
  }])
}

# ---------- Service: keep 1 task running, public IP so you can reach it ----------
resource "aws_ecs_service" "dashboard" {
  name            = "drift-dashboard"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.dashboard.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.dashboard.id]
    assign_public_ip = true          # <-- REQUIRED to reach it without a load balancer
  }
}