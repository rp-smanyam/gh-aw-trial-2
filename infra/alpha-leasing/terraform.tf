terraform {
  backend "s3" {
    bucket = "knock-cc-alpha-tf-state"
    key    = "terraform/agent-leasing"
    region = "us-east-1"
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = var.tags
  }
}

# This can be removed as we are pointing to the alpha ECR repo and not the Shared services repo - Confirm on this
provider "aws" {
  alias  = "shared-networking"
  region = "us-east-1"
  default_tags {
    tags = var.tags
  }
  assume_role {
    role_arn = "arn:aws:iam::688951274555:role/nonprod-dns-update"
  }
}

# This can be removed as we are pointing to the alpha ECR repo and not the Shared services repo - Confirm on this
provider "aws" {
  alias  = "ecr"
  region = "us-east-1"

  assume_role {
    role_arn = "arn:aws:iam::171267611104:role/shse-infra-setup"
  }
  default_tags {
    tags = var.tags
  }
}
