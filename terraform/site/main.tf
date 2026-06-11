data "aws_caller_identity" "current" {}

locals {
  bucket_name = var.bucket_name != "" ? var.bucket_name : "snowmelt-ai-site-${data.aws_caller_identity.current.account_id}"
  use_domain  = var.domain_name != ""

  # Files to upload + a minimal content-type map (extend as needed).
  content_files = fileset("${path.module}/${var.content_dir}", "**")
  mime = {
    html  = "text/html"
    css   = "text/css"
    js    = "application/javascript"
    svg   = "image/svg+xml"
    png   = "image/png"
    jpg   = "image/jpeg"
    jpeg  = "image/jpeg"
    gif   = "image/gif"
    ico   = "image/x-icon"
    json  = "application/json"
    txt   = "text/plain"
    woff  = "font/woff"
    woff2 = "font/woff2"
  }

  origin_id = "s3-${local.bucket_name}"
}

###############################################################################
# Private S3 bucket — origin for CloudFront (no public access; OAC only)
###############################################################################

resource "aws_s3_bucket" "site" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload every file under content/ with a sensible content-type.
resource "aws_s3_object" "content" {
  for_each = local.content_files

  bucket       = aws_s3_bucket.site.id
  key          = each.value
  source       = "${path.module}/${var.content_dir}/${each.value}"
  etag         = filemd5("${path.module}/${var.content_dir}/${each.value}")
  content_type = lookup(local.mime, lower(reverse(split(".", each.value))[0]), "application/octet-stream")
}

###############################################################################
# CloudFront — global CDN + HTTPS in front of the private bucket
###############################################################################

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name}-oac"
  description                       = "OAC for ${local.bucket_name}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# AWS-managed CachingOptimized policy (good defaults for static sites).
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = var.name
  default_root_object = var.default_root_object
  price_class         = var.price_class
  aliases             = local.use_domain ? concat([var.domain_name], var.subject_alternative_names) : []

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = local.origin_id
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = local.origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
  }

  # Single-page friendly: serve index.html for 403/404 from S3.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/${var.default_root_object}"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/${var.default_root_object}"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.use_domain ? null : true
    acm_certificate_arn            = one(aws_acm_certificate_validation.site[*].certificate_arn)
    ssl_support_method             = local.use_domain ? "sni-only" : null
    minimum_protocol_version       = local.use_domain ? "TLSv1.2_2021" : null
  }
}

# Bucket policy: allow ONLY this CloudFront distribution (via OAC) to read objects.
resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn }
      }
    }]
  })
}

###############################################################################
# ACM certificate — only when a custom domain is configured (us-east-1).
# DNS-validated; add the CNAMEs from the `acm_validation_records` output to your
# DNS (IONOS or Route 53). apply waits here until the cert is validated.
###############################################################################

resource "aws_acm_certificate" "site" {
  count                     = local.use_domain ? 1 : 0
  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "site" {
  count           = local.use_domain ? 1 : 0
  certificate_arn = aws_acm_certificate.site[0].arn
}
