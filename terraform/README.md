# snowmelt — Terraform: dev node + Elastic IP

Replayable Terraform to **set up** / **tear down** a single AWS compute node (default:
`m7g.8xlarge` Graviton3 **spot** instance in `us-east-2c`), fronted by a **stable Elastic IP**.

Split into **two independent stacks with separate state** so the IP survives node teardown:

```
terraform/
├── eip/     # long-lived Elastic IP — apply ONCE, leave it. Holds the address.
└── node/    # disposable instance + SG + EIP association — apply/destroy freely.
```

`node` *discovers* the EIP by its `Name` tag and associates it. Destroying `node` removes
only the association — the EIP (and its address, e.g. what `snowmelt.ai` points at) stays put.

---

## What each stack manages

| Stack | Resources | Lifecycle |
|---|---|---|
| **eip/** | `aws_eip` (tagged `snowmelt-dev-eip`, `prevent_destroy`) | Create once; keep |
| **node/** | `aws_instance` (spot), `aws_security_group`, `aws_eip_association` | Create/destroy often |

`node/` also *reads* (never creates) the default VPC, the default subnet in the AZ, the
AL2023 arm64 AMI, and the EIP (by tag).

> **Not included (yet):** infra only. Kubernetes/Traefik were installed manually over SSH
> and are **not** managed here. See "Next steps".

---

## Prerequisites

1. **Terraform** `>= 1.5` — `brew install hashicorp/tap/terraform`
2. **AWS credentials** for account `477537170386`:
   ```bash
   aws sts get-caller-identity      # should succeed
   ```
   > ⚠️ **`aws login` users (this account):** your CLI authenticates via the `aws login`
   > token cache (`~/.aws/login/`), which **Terraform cannot read** — a bare `terraform`
   > command fails with *"No valid credential sources found"*. Bridge the creds into env
   > vars for the current shell **before any terraform command**:
   > ```bash
   > eval "$(aws configure export-credentials --format env)"
   > ```
   > These are temporary; re-run if Terraform reports an auth/expiry error.
3. **An existing EC2 key pair** named by `var.key_name` (default `m7g-us-east-2c`, exists).
   To make a new one:
   ```bash
   aws ec2 create-key-pair --region us-east-2 --key-name my-key \
     --query KeyMaterial --output text > ~/.ssh/my-key.pem && chmod 400 ~/.ssh/my-key.pem
   # then set key_name = "my-key" in node/terraform.tfvars
   ```

---

## First-time setup

```bash
cd ~/Work/next/terraform
eval "$(aws configure export-credentials --format env)"   # bridge aws-login creds

# 1. Create the Elastic IP (ONCE). Note the address it prints.
cd eip
terraform init
terraform apply
terraform output public_ip        # <- your stable IP; point DNS here later

# 2. Lock node ingress to your current IP, then create the node.
cd ../node
cp terraform.tfvars.example terraform.tfvars
echo "allowed_cidr = \"$(curl -s https://checkip.amazonaws.com)/32\"" >> terraform.tfvars
terraform init
terraform apply
terraform output -raw ssh_command    # ssh -i ~/.ssh/<key>.pem ec2-user@<EIP>
```

> ⚠️ Apply **`eip` before `node`** — `node` looks the EIP up by tag and fails if it
> doesn't exist yet. Both stacks must share the same `region` and `name` (defaults match).

---

## Day-to-day: recycle the node, keep the IP

```bash
cd ~/Work/next/terraform/node
eval "$(aws configure export-credentials --format env)"

terraform destroy     # TEAR DOWN the instance — EIP is untouched, address preserved
terraform apply       # SET UP a fresh instance — re-associates the SAME EIP
```

The `eip` stack just sits there holding the address. You normally never touch it again.

---

## Common tweaks (`node/terraform.tfvars`)

| Want to… | Set |
|---|---|
| On-demand instead of spot | `use_spot = false` |
| Raise/lower spot cap | `spot_max_price = "0.65"` |
| Different size | `instance_type = "m7g.16xlarge"` |
| Different AZ (capacity!) | `availability_zone = "us-east-2a"` |
| Restrict open ports | `ingress_ports = [22, 443]` |
| Allow from your new IP | `allowed_cidr = "<ip>/32"` |

---

## Important caveats

- **Spot interruption:** `use_spot = true` lets AWS reclaim the node (~2 min notice); it
  terminates and state shows drift. Re-`apply` for a new one. Use `use_spot = false` for
  anything you can't lose.
- **The EIP is guarded.** `eip/` has `prevent_destroy = true`, so `terraform destroy` there
  errors on purpose. To actually release it, remove that `lifecycle` block first.
- **Public IPv4 cost:** the EIP costs ~$0.005/hr (~$3.60/mo) whether attached or not.
- **`allowed_cidr` default is a fixed IP** captured at setup. Your IP changes — update it
  (the `curl` one-liner above) or SSH/web access silently breaks.

---

## Adopting the EXISTING (manually-created) instance

The running node `i-0fd0d548e8589faee` was created by hand and is **not** in Terraform
state. Either:
- **Fresh start:** terminate it and let `node` own a new instance (reinstall Kubernetes), or
- **Import:** `terraform import aws_instance.node i-0fd0d548e8589faee` (then reconcile diffs —
  it used the default SG and has no EIP).

---

## Next steps (not yet implemented)

- **`user_data`** bootstrap to auto-install containerd/kubeadm/Traefik on `apply`.
- **Remote state** (S3 + DynamoDB lock) so the stacks are shareable/CI-friendly.
- **Multi-node + NLB/ACM** for a real `snowmelt.ai` production topology.
