module "leasing" {
  source              = "git@github.com:knockrentals/tf-ecs-service//?ref=v3.0.31"
  accepts_connections = true
  is_internal_service = false
  application         = var.application
  environment         = var.environment
  cluster_name        = var.cluster
  vpc_name            = var.vpc_name
  is_awsvpc           = "false"

  service_min_count = 2
  service_max_count = 12

  deployment_max_percent = 200
  deployment_min_percent = 100

  health_check_grace_period_seconds      = 360
  autoscale_type                         = "target_tracking"
  target_tracking_target_value           = 30
  target_tracking_predefined_metric_name = "ALBRequestCountPerTarget"
  target_tracking_scale_in_cooldown      = 30
  target_tracking_scale_out_cooldown     = 30

  container_target_port      = 8080
  container_target_name      = "${var.environment}-${var.application}-nginx"
  deregistration_delay       = 5
  container_healthcheck_path = "/healthcheck"

  external_alb_name  = "ccnp-external-alb"
  internal_alb_name  = "ccnp-internal-alb"
  host_header_values = ["beta-agent-leasing.knocktest.com"]

  # Added SM permissions to the task role
  task_role_policy = [{
    Effect   = "Allow"
    Action   = ["secretsmanager:*"]
    Resource = "arn:aws:secretsmanager:*"
  }]

  execution_role_policy = [
    {
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = ["arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:parameter/${var.environment}/${var.application}/*"]
    },
    {
      Effect   = "Allow"
      Action   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
      Resource = ["arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:agent-leasing-${var.secrets_hash}"]
    }
  ]

  github_org       = "RealPage"
  github_repo_name = var.github_repo_name
  image_repo_names = var.image_repo_names

  additional_github_upload_permissions = []
  additional_github_deploy_permissions = [
    # Voice service permissions
    {
      Effect = "Allow"
      Action = ["ecs:UpdateService", "ecs:DescribeServices"]
      Resource = [
        "arn:aws:ecs:us-east-1:${data.aws_caller_identity.current.account_id}:service/${var.cluster}/beta-agent-leasing-voice"
      ]
    },
    {
      Effect = "Allow"
      Action = ["iam:PassRole"]
      Resource = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/beta-agent-leasing-voice-task-role",
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/beta-agent-leasing-voice-execution-role"
      ]
    },
    # LDP cache warmer Lambda deploy permissions
    {
      Effect = "Allow"
      Action = [
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode"
      ]
      Resource = [
        "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:${var.environment}-ldp-cache-warmer",
        "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:${var.environment}-ldp-cache-warmer-worker"
      ]
    },
    # Pull secrets Manager content for task definition generation
    {
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ]
      Resource = [
        "arn:aws:secretsmanager:us-east-1:${var.aws_account}:secret:agent-leasing-${var.secrets_hash}"
      ]
    }
  ]

  providers = {
    aws.dns = aws.shared-networking
    aws.ecr = aws.ecr
  }
}
