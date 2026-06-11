output "allocation_id" {
  description = "EIP allocation ID (eipalloc-...)."
  value       = aws_eip.this.id
}

output "public_ip" {
  description = "The stable public IP address. Point DNS (e.g. snowmelt.ai) here."
  value       = aws_eip.this.public_ip
}

output "name_tag" {
  description = "Name tag the node stack uses to discover this EIP."
  value       = "${var.name}-eip"
}
