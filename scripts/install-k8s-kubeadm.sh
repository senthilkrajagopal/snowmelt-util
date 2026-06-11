#!/usr/bin/env bash
#
# Single-node vanilla Kubernetes (kubeadm) installer for an Ubuntu/Debian VPS.
#
#   - containerd runtime (SystemdCgroup)
#   - kubeadm / kubelet / kubectl pinned to the v1.36 package channel
#   - Calico CNI
#   - control-plane node untainted so workloads schedule on the single node
#   - Helm + local-path-provisioner (default StorageClass) for Helm charts w/ PVCs
#
# Run as root on the SERVER (not from your laptop):
#   curl -fsSL <url-or-scp-this-file> -o install-k8s.sh
#   sudo bash install-k8s.sh
#
# Idempotent-ish: safe to re-run after fixing a failed step. To start over,
# run `kubeadm reset -f` first.
#
set -euo pipefail

### ---- tunables -------------------------------------------------------------
K8S_MINOR="v1.36"                 # package repo channel (pkgs.k8s.io)
POD_CIDR="192.168.0.0/16"         # Calico default; must not overlap your VPC
CALICO_VERSION="v3.29.1"          # CNI release
LOCALPATH_VERSION="v0.0.30"       # rancher local-path-provisioner
### --------------------------------------------------------------------------

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash $0)."
command -v apt-get >/dev/null || die "This script targets Debian/Ubuntu (apt). Detected non-apt OS."

ARCH="$(dpkg --print-architecture)"   # amd64 / arm64
log "Detected arch: ${ARCH}"

# 1) Kernel prerequisites ----------------------------------------------------
log "Disabling swap (required by kubelet)"
swapoff -a
sed -i.bak -E 's@^([^#].*\sswap\s.*)$@#\1@' /etc/fstab || true

log "Loading kernel modules"
cat >/etc/modules-load.d/k8s.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter

log "Setting sysctl for bridged traffic + forwarding"
cat >/etc/sysctl.d/99-k8s.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system >/dev/null

# 2) containerd --------------------------------------------------------------
log "Installing containerd + tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gnupg socat conntrack containerd

log "Configuring containerd with SystemdCgroup=true"
mkdir -p /etc/containerd
containerd config default >/etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd

# 3) kube* packages ----------------------------------------------------------
log "Adding Kubernetes apt repo (${K8S_MINOR})"
mkdir -p /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/Release.key" \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/ /" \
  >/etc/apt/sources.list.d/kubernetes.list

log "Installing kubeadm, kubelet, kubectl"
apt-get update -y
apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl
systemctl enable kubelet

# 4) Pull images + init ------------------------------------------------------
log "Pre-pulling control-plane images"
kubeadm config images pull

if [ -f /etc/kubernetes/admin.conf ]; then
  log "admin.conf already exists — skipping kubeadm init (cluster looks initialized)"
else
  log "Running kubeadm init (pod-network-cidr=${POD_CIDR})"
  kubeadm init --pod-network-cidr="${POD_CIDR}"
fi

# 5) kubeconfig --------------------------------------------------------------
log "Wiring kubeconfig for root"
export KUBECONFIG=/etc/kubernetes/admin.conf
mkdir -p /root/.kube
cp -f /etc/kubernetes/admin.conf /root/.kube/config
chown "$(id -u)":"$(id -g)" /root/.kube/config
# also for the invoking sudo user, if any
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  uhome="$(eval echo "~${SUDO_USER}")"
  mkdir -p "${uhome}/.kube"
  cp -f /etc/kubernetes/admin.conf "${uhome}/.kube/config"
  chown -R "${SUDO_USER}":"${SUDO_USER}" "${uhome}/.kube"
fi

# 6) CNI ---------------------------------------------------------------------
log "Installing Calico CNI ${CALICO_VERSION}"
kubectl apply -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml"

# 7) Single-node: untaint control plane --------------------------------------
log "Removing control-plane taint so pods schedule on this single node"
kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true

# 8) Helm --------------------------------------------------------------------
log "Installing Helm"
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# 9) Storage: local-path-provisioner as default StorageClass ------------------
log "Installing local-path-provisioner ${LOCALPATH_VERSION} (default StorageClass)"
kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCALPATH_VERSION}/deploy/local-path-storage.yaml"
kubectl patch storageclass local-path \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}' || true

# 10) Wait + report ----------------------------------------------------------
log "Waiting for node to become Ready (up to 180s)"
kubectl wait --for=condition=Ready node --all --timeout=180s || true

log "Done. Cluster status:"
kubectl get nodes -o wide || true
echo
kubectl get pods -A || true

cat <<'EOF'

============================================================
 Kubernetes single-node cluster is up.

 As root, kubectl is ready (KUBECONFIG=/root/.kube/config).
 Quick checks:
   kubectl get nodes
   kubectl get pods -A
   kubectl get storageclass

 Deploy the Snowmelt chart (from the repo root on this box):
   helm install snowmelt ./charts/snowmelt

 To tear everything down and start over:
   kubeadm reset -f && rm -rf /etc/cni/net.d ~/.kube
============================================================
EOF
