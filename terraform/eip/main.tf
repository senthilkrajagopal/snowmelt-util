###############################################################################
# Long-lived Elastic IP — lives in its OWN state so it survives `destroy` of
# the node stack. The node stack discovers it by the Name tag and associates it.
###############################################################################

resource "aws_eip" "this" {
  domain = "vpc"

  tags = {
    Name = "${var.name}-eip"
  }

  # Guard against accidental release (which would change the address and break
  # any DNS pointed at it). Remove this block if you intentionally want to
  # `terraform destroy` the EIP.
  lifecycle {
    prevent_destroy = true
  }
}
