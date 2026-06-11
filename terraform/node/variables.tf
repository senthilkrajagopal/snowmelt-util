variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "availability_zone" {
  description = "Availability zone for the instance. Must have capacity for the chosen instance type."
  type        = string
  default     = "us-east-2c"
}

variable "name" {
  description = "Name prefix applied to created resources (instance, EIP, security group)."
  type        = string
  default     = "snowmelt-dev"
}

variable "instance_type" {
  description = "EC2 instance type. Default is the Graviton3 m7g.8xlarge (32 vCPU / 128 GiB, arm64)."
  type        = string
  default     = "m7g.8xlarge"
}

variable "spot_max_price" {
  description = "Maximum hourly spot price (USD). The request will not be fulfilled above this. Set empty string \"\" to use the on-demand price as the cap."
  type        = string
  default     = "0.50"
}

variable "use_spot" {
  description = "If true, launch as a Spot instance (cheaper, interruptible). If false, launch on-demand (stable, pricier)."
  type        = bool
  default     = true
}

variable "key_name" {
  description = "Name of an EXISTING EC2 key pair to attach for SSH. Must already exist in the region (see README to create one)."
  type        = string
  default     = "m7g-us-east-2c"
}

variable "root_volume_size" {
  description = "Root EBS volume size in GiB."
  type        = number
  default     = 50
}

variable "allowed_cidr" {
  description = "CIDR allowed to reach the instance on the ingress ports (your public IP /32 recommended — set it in terraform.tfvars). Do NOT use 0.0.0.0/0 unless you mean it."
  type        = string
  # No default on purpose: the previous default leaked a personal IP into the
  # repo, and any default silently scopes ingress to a stranger's address.
}

variable "ingress_ports" {
  description = "TCP ports to open from allowed_cidr. 22=SSH, 80/443=web, 30080/30443=Traefik NodePorts."
  type        = list(number)
  default     = [22, 80, 443, 30080, 30443]
}
