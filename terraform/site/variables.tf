variable "region" {
  description = "Region for the S3 bucket and ACM cert. Keep us-east-1 (required for CloudFront ACM certs)."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for resources."
  type        = string
  default     = "snowmelt-site"
}

variable "bucket_name" {
  description = "Globally-unique S3 bucket name. Leave empty to auto-derive snowmelt-ai-site-<account_id>."
  type        = string
  default     = ""
}

variable "content_dir" {
  description = "Local directory whose files are uploaded to the site root. index.html must exist."
  type        = string
  default     = "content"
}

variable "default_root_object" {
  description = "Object served at /."
  type        = string
  default     = "index.html"
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_100 (NA+EU) is cheapest; _All is global."
  type        = string
  default     = "PriceClass_100"
}

# ----- Custom domain (optional). Leave domain_name = "" to deploy on the *.cloudfront.net
# URL immediately with no DNS/cert setup. Set it later to attach snowmelt.ai (see README). -----

variable "domain_name" {
  description = "Primary custom domain (e.g. snowmelt.ai). Empty = use the default CloudFront domain only."
  type        = string
  default     = ""
}

variable "subject_alternative_names" {
  description = "Extra hostnames for the cert/CloudFront aliases (e.g. [\"www.snowmelt.ai\"])."
  type        = list(string)
  default     = []
}
