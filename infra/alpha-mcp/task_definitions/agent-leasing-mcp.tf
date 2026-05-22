resource "local_file" "agent_leasing" {
  filename = "agent-leasing-mcp.json"

  content = jsonencode({
    containerDefinitions = [
      {
        name              = "${var.environment}-${var.application}-nginx"
        image             = "171267611104.dkr.ecr.us-east-1.amazonaws.com/nginx-proxy:proxy-pass-static-hc"
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
            containerPort = 8080,
            protocol      = "tcp"
          }
        ],
        links = [
          "${var.environment}-${var.application}:${var.application}"
        ]
        environment = [
          {
            name  = "LISTEN_PORT",
            value = "8080"
          },
          {
            name  = "APP_PORT",
            value = "8042"
          },
          {
            name  = "APP_HOST",
            value = "http://${var.application}"
          }
        ],
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
        cpu    = 512
        memory = 8192
        portMappings = [
          {
            containerPort = 8000
            protocol      = "tcp"
          }
        ]
        environment = [
          {
            name  = "DEBUG_FLAG",
            value = "true"
          },
          {
            name  = "APP_NAME",
            value = "${var.application}"
          },
          {
            name  = "ENVIRONMENT",
            value = "${var.environment}"
          },
          {
            name  = "LANGCHAIN_PROJECT",
            value = "${var.environment}-${var.application}"
          },
          {
            name  = "LANGCHAIN_TRACING_V2",
            value = "true"
          }
        ]
        secrets = local.secrets
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = "/ecs/alpha-agent-leasing"
            awslogs-region        = "us-east-1"
            awslogs-stream-prefix = "app"
          }
        }
      }
    ]
    family           = "${var.environment}-${var.application}-mcp"
    networkMode      = "bridge"
    taskRoleArn      = "arn:aws:iam::${var.aws_account}:role/${var.environment}-${var.application}-mcp-task-role"
    executionRoleArn = "arn:aws:iam::${var.aws_account}:role/${var.environment}-${var.application}-mcp-execution-role"
  })
}
