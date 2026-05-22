module "mcp" {
  source              = "git@github.com:knockrentals/tf-ecs-service//?ref=v3.0.21"
  accepts_connections = true
  is_internal_service = true
  application         = "${var.application}-mcp"
  environment         = var.environment
  cluster_name        = var.cluster
  vpc_name            = var.vpc_name
  is_awsvpc           = "false"
  akamai_enabled      = false

  service_min_count      = 1
  service_max_count      = 2
  deployment_max_percent = 200
  deployment_min_percent = 100

  health_check_grace_period_seconds      = 360
  autoscale_type                         = "target_tracking"
  target_tracking_target_value           = 66
  target_tracking_predefined_metric_name = "ECSServiceAverageMemoryUtilization"
  target_tracking_scale_in_cooldown      = 30
  target_tracking_scale_out_cooldown     = 30

  container_target_port      = 8080
  container_target_name      = "${var.environment}-${var.application}-nginx"
  deregistration_delay       = 5
  container_healthcheck_path = "/health"

  external_alb_name  = "${var.environment}-external-alb"
  internal_alb_name  = "${var.environment}-internal-alb"
  host_header_values = ["alpha-agent-leasing-mcp.knocktest.com"]

  # Added SM permissions to the task role
  task_role_policy = [{
    Effect   = "Allow"
    Action   = ["secretsmanager:*"]
    Resource = "arn:aws:secretsmanager:*"
  }]

  execution_role_policy = [{
    Effect   = "Allow"
    Action   = ["ssm:GetParameters"]
    Resource = ["arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:parameter/${var.environment}/${var.application}/*"]
  }]

  github_org = "RealPage"
  github_repo_name = var.github_repo_name
  image_repo_names = var.image_repo_names

  create_github_actions_roles = false

  additional_github_upload_permissions = []
  additional_github_deploy_permissions = []

  providers = {
    aws.dns = aws.shared-networking
    aws.ecr = aws.ecr
  }
}
