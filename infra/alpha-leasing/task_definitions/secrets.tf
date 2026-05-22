data "aws_secretsmanager_secret_version" "app_secrets" {
  secret_id = "arn:aws:secretsmanager:us-east-1:${var.aws_account}:secret:agent-leasing-${var.secrets_hash}"
}

locals {
  secrets = keys(jsondecode(data.aws_secretsmanager_secret_version.app_secrets.secret_string))
}
