provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "snowmelt"
      ManagedBy = "terraform"
      Stack     = "next/terraform/eip"
    }
  }
}
