###############################################################################
# Data sources — discover the default VPC/subnet and the latest AL2023 arm64 AMI
###############################################################################

data "aws_vpc" "default" {
  default = true
}

# Default subnet in the chosen AZ (matches how the instance was launched manually).
data "aws_subnet" "selected" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Latest Amazon Linux 2023 arm64 AMI, resolved per-region via SSM Parameter Store.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

###############################################################################
# Security group — scoped ingress from allowed_cidr only
###############################################################################

resource "aws_security_group" "instance" {
  name        = "${var.name}-sg"
  description = "Ingress for ${var.name} (SSH + web + Traefik NodePorts) from allowed_cidr"
  vpc_id      = data.aws_vpc.default.id

  dynamic "ingress" {
    for_each = toset(var.ingress_ports)
    content {
      description = "port ${ingress.value} from allowed_cidr"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = [var.allowed_cidr]
    }
  }

  egress {
    description = "allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name}-sg"
  }
}

###############################################################################
# Compute — the (spot or on-demand) instance
###############################################################################

resource "aws_instance" "node" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.selected.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  key_name               = var.key_name

  # Spot configuration (only rendered when use_spot = true).
  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        max_price                      = var.spot_max_price
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = var.name
  }
}

###############################################################################
# Elastic IP — owned by the separate `../eip` stack. We DISCOVER it by Name tag
# and associate it with this instance. Destroying this stack removes only the
# association; the EIP itself (and its address) survives in the eip stack.
#
# Prerequisite: apply the ../eip stack first, or this lookup finds nothing.
###############################################################################

data "aws_eip" "node" {
  tags = {
    Name = "${var.name}-eip"
  }
}

resource "aws_eip_association" "node" {
  instance_id   = aws_instance.node.id
  allocation_id = data.aws_eip.node.id
}
