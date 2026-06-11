variable "region" {
  description = "AWS region. Must match the node stack's region."
  type        = string
  default     = "us-east-2"
}

variable "name" {
  description = "Name prefix. The EIP is tagged Name=\"<name>-eip\"; the node stack looks it up by that tag. Must match the node stack's name."
  type        = string
  default     = "snowmelt-dev"
}
