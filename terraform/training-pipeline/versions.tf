# Той самий пін, що й у terraform/cluster: обидва корені мають поводитись
# однаково. Версія Terraform береться з .terraform-version через tfenv.
terraform {
  required_version = "~> 1.15"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.52"
    }
    # archive збирає ZIP для Lambda прямо під час plan/apply — без Makefile,
    # без окремого кроку збірки і без бінарників у Git.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "aws" {
  region = var.region
}
