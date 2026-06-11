output "bucket" {
  description = "S3 bucket holding the site content."
  value       = aws_s3_bucket.site.id
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain (always works, even without a custom domain)."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "distribution_id" {
  description = "CloudFront distribution ID (use for cache invalidations on content updates)."
  value       = aws_cloudfront_distribution.site.id
}

output "url" {
  description = "Primary URL for the site."
  value       = local.use_domain ? "https://${var.domain_name}/" : "https://${aws_cloudfront_distribution.site.domain_name}/"
}

# DNS guidance (only meaningful when domain_name is set) -----------------------

output "dns_target" {
  description = "Point your domain's ALIAS/A (apex) or CNAME (www) at this CloudFront hostname."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "acm_validation_records" {
  description = "CNAMEs to add at your DNS provider to validate the certificate (empty until domain_name is set)."
  value = local.use_domain ? [
    for o in aws_acm_certificate.site[0].domain_validation_options : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ] : []
}
