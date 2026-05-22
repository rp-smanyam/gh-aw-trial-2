# =============================================================================
# LDP Cache Warmer — Two-Lambda + SQS Architecture
# =============================================================================
# Producer Lambda (EventBridge → SQS): fetches property IDs, chunks, sends to queue
# Worker Lambda (SQS → Redis): warms LDP cache for each property batch
#
# Both use the AWS Secrets Manager Lambda Extension to fetch secrets
# instead of hardcoded environment variables.

# -----------------------------------------------------------------------------
# Placeholder zip for initial TF apply — CI replaces with real code
# -----------------------------------------------------------------------------
data "archive_file" "lambda_placeholder" {
  type        = "zip"
  output_path = "${path.module}/lambda_placeholder.zip"
  source {
    content  = <<-PYTHON
def producer_handler(event, context):
    return {"statusCode": 200, "body": "placeholder"}

def worker_handler(event, context):
    return {"batchItemFailures": []}
PYTHON
    filename = "handler.py"
  }
}

# -----------------------------------------------------------------------------
# VPC data sources — look up subnets from ElastiCache subnet group
# -----------------------------------------------------------------------------
data "aws_elasticache_subnet_group" "cache" {
  name = aws_elasticache_cluster.agent_leasing.subnet_group_name
}

data "aws_subnets" "elasticache" {
  filter {
    name   = "subnet-id"
    values = data.aws_elasticache_subnet_group.cache.subnet_ids
  }
}

# Look up a single subnet to get the VPC ID (aws_subnets doesn't expose vpc_id)
data "aws_subnet" "first_elasticache" {
  id = data.aws_subnets.elasticache.ids[0]
}

# -----------------------------------------------------------------------------
# Security group — Lambda needs outbound internet + Redis access
# -----------------------------------------------------------------------------
resource "aws_security_group" "lambda_cache_warmer" {
  name        = "${var.environment}-ldp-cache-warmer-lambda"
  description = "SG for LDP cache warmer Lambda"
  vpc_id      = data.aws_subnet.first_elasticache.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.tags
}

# Allow Lambda -> Redis on port 6379
resource "aws_security_group_rule" "lambda_to_redis" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda_cache_warmer.id
  security_group_id        = local.redis_security_group_id
}

# -----------------------------------------------------------------------------
# IAM role — shared by producer and worker Lambdas
# -----------------------------------------------------------------------------
resource "aws_iam_role" "ldp_cache_warmer" {
  name = "${var.environment}-ldp-cache-warmer-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = var.tags
}

# VPC access (ENI management) + CloudWatch logs.
# Both Lambdas run in the VPC, so the shared role needs ENI permissions.
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.ldp_cache_warmer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Secrets Manager — SM extension needs read access to the shared secret
resource "aws_iam_role_policy" "lambda_secrets" {
  name = "${var.environment}-ldp-cache-warmer-secrets"
  role = aws_iam_role.ldp_cache_warmer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = "arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:agent-leasing-${var.secrets_hash}*"
    }]
  })
}

# SQS — producer sends, worker receives
resource "aws_iam_role_policy" "lambda_sqs" {
  name = "${var.environment}-ldp-cache-warmer-sqs"
  role = aws_iam_role.ldp_cache_warmer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:SendMessage",
        "sqs:SendMessageBatch",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
      ]
      Resource = [aws_sqs_queue.ldp_cache_warmer.arn]
    }]
  })
}

# -----------------------------------------------------------------------------
# SQS queue + dead letter queue
# -----------------------------------------------------------------------------
resource "aws_sqs_queue" "ldp_cache_warmer_dlq" {
  name                      = "${var.environment}-ldp-cache-warmer-dlq"
  message_retention_seconds = 1209600 # 14 days

  tags = var.tags
}

resource "aws_sqs_queue" "ldp_cache_warmer" {
  name                       = "${var.environment}-ldp-cache-warmer"
  visibility_timeout_seconds = 180   # >= worker Lambda timeout
  message_retention_seconds  = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ldp_cache_warmer_dlq.arn
    maxReceiveCount     = 3
  })

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Producer Lambda (EventBridge → SQS)
# -----------------------------------------------------------------------------
resource "aws_lambda_function" "ldp_cache_warmer" {
  function_name = "${var.environment}-ldp-cache-warmer"
  role          = aws_iam_role.ldp_cache_warmer.arn
  handler       = "handler.producer_handler"
  runtime       = "python3.13"
  timeout       = 60
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  # CI/CD deploys the real code; TF just bootstraps with a placeholder
  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  # Producer runs in the VPC because AI Config depends on VPC-reachable
  # networking in practice. Reuse the worker subnets and SG.
  vpc_config {
    subnet_ids         = data.aws_subnets.elasticache.ids
    security_group_ids = [aws_security_group.lambda_cache_warmer.id]
  }

  layers = [
    "arn:aws:lambda:us-east-1:177933569100:layer:AWS-Parameters-and-Secrets-Lambda-Extension:14"
  ]

  environment {
    variables = {
      SECRETS_MANAGER_SECRET_ID = "agent-leasing"
      SQS_QUEUE_URL             = aws_sqs_queue.ldp_cache_warmer.url
    }
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Worker Lambda (SQS → Redis)
# -----------------------------------------------------------------------------
resource "aws_lambda_function" "ldp_cache_warmer_worker" {
  function_name = "${var.environment}-ldp-cache-warmer-worker"
  role          = aws_iam_role.ldp_cache_warmer.arn
  handler       = "handler.worker_handler"
  runtime       = "python3.13"
  timeout       = 120
  memory_size   = 256

  reserved_concurrent_executions = 5

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  vpc_config {
    subnet_ids         = data.aws_subnets.elasticache.ids
    security_group_ids = [aws_security_group.lambda_cache_warmer.id]
  }

  layers = [
    "arn:aws:lambda:us-east-1:177933569100:layer:AWS-Parameters-and-Secrets-Lambda-Extension:14"
  ]

  environment {
    variables = {
      SECRETS_MANAGER_SECRET_ID = "agent-leasing"
      REDIS_HOST                = aws_elasticache_cluster.agent_leasing.cache_nodes[0].address
      REDIS_PORT                = "6379"
    }
  }

  tags = var.tags
}

# SQS → Worker event source mapping
resource "aws_lambda_event_source_mapping" "ldp_cache_warmer" {
  event_source_arn                   = aws_sqs_queue.ldp_cache_warmer.arn
  function_name                      = aws_lambda_function.ldp_cache_warmer_worker.arn
  batch_size                         = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0
}

# -----------------------------------------------------------------------------
# EventBridge schedule — every 30 minutes (triggers producer)
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "ldp_cache_warmer" {
  name                = "${var.environment}-ldp-cache-warmer-schedule"
  schedule_expression = "rate(30 minutes)"

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "ldp_cache_warmer" {
  rule = aws_cloudwatch_event_rule.ldp_cache_warmer.name
  arn  = aws_lambda_function.ldp_cache_warmer.arn
}

resource "aws_lambda_permission" "eventbridge" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ldp_cache_warmer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ldp_cache_warmer.arn
}
