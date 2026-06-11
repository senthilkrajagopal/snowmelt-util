# Region is us-east-1 so the same provider serves S3 AND the ACM certificate
# (CloudFront requires its ACM cert to live in us-east-1). CloudFront itself is global.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "snowmelt"
      ManagedBy = "terraform"
      Stack     = "next/terraform/site"
    }
  }
}
