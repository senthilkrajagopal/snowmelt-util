output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.node.id
}

output "instance_type" {
  description = "Instance type launched."
  value       = aws_instance.node.instance_type
}

output "availability_zone" {
  description = "AZ the instance landed in."
  value       = aws_instance.node.availability_zone
}

output "eip" {
  description = "Stable public Elastic IP (owned by the ../eip stack). Point DNS (e.g. snowmelt.ai) at this."
  value       = data.aws_eip.node.public_ip
}

output "private_ip" {
  description = "Private IP within the VPC."
  value       = aws_instance.node.private_ip
}

output "security_group_id" {
  description = "Security group protecting the instance."
  value       = aws_security_group.instance.id
}

output "ssh_command" {
  description = "Ready-to-paste SSH command (assumes the key .pem is in ~/.ssh)."
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ec2-user@${data.aws_eip.node.public_ip}"
}
