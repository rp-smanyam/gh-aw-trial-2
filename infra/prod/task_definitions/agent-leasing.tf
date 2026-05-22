resource "local_file" "agent_leasing" {
  filename = "agent-leasing.json"

  content = jsonencode({
    containerDefinitions = [
      {
        name              = "${var.environment}-${var.application}-nginx"
        image             = "171267611104.dkr.ecr.us-east-1.amazonaws.com/nginx-proxy:proxy-pass-websocket"
        memory            = 256
        memoryReservation = 64
        cpu               = 64
        linuxParameters = {
          tmpfs = [
            {
              containerPath = "/tmp"
              mountOptions  = ["rw"]
              size          = 64
            },
            {
              containerPath = "/etc/nginx/conf.d"
              mountOptions  = ["rw"]
              size          = 1
            }
          ]
        }
        portMappings = [
          {
            containerPort = 8080
            protocol      = "tcp"
          }
        ]
        links = [
          "${var.environment}-${var.application}:${var.application}"
        ]
        environment = [
          {
            name  = "LISTEN_PORT"
            value = "8080"
          },
          {
            name  = "APP_PORT"
            value = "8000"
          },
          {
            name  = "APP_HOST"
            value = "${var.application}"
          }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = "/ecs/${var.environment}-${var.application}"
            awslogs-region        = "us-east-1"
            awslogs-stream-prefix = "nginx"
          }
        }
      },
      {
        name   = "${var.environment}-agent-leasing"
        image  = "171267611104.dkr.ecr.us-east-1.amazonaws.com/agent-leasing:${var.app_image_tag}"
        cpu    = 1024
        memory = 8192
        portMappings = [
          {
            containerPort = 8000
            protocol      = "tcp"
          }
        ]
        environment = [
          {
            name  = "LOG_JSON_FORMAT"
            value = "true"
          },
          {
            name  = "ENVIRONMENT"
            value = "${var.environment}"
          },
          {
            name  = "LANGSMITH_OTEL_ENABLED"
            value = "true"
          },
          {
            name  = "OTEL_BSP_MAX_EXPORT_BATCH_SIZE"
            value = "128"
          },
          {
            name  = "OTEL_TRACES_SAMPLER"
            value = "always_on"
          }
        ]
        secrets = [for secret in local.secrets :
          {
            name      = secret
            valueFrom = "arn:aws:secretsmanager:us-east-1:${var.aws_account}:secret:agent-leasing-${var.secrets_hash}:${secret}::"
          }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = "/ecs/${var.environment}-${var.application}"
            awslogs-region        = "us-east-1"
            awslogs-stream-prefix = "app"
          }
        }
      }
    ]
    family           = "${var.environment}-${var.application}"
    networkMode      = "bridge"
    taskRoleArn      = "arn:aws:iam::${var.aws_account}:role/${var.environment}-${var.application}-task-role"
    executionRoleArn = "arn:aws:iam::${var.aws_account}:role/${var.environment}-${var.application}-execution-role"
  })
}
