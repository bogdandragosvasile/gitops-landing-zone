#!/usr/bin/env bash
# Install prerequisites: k3d, helm, tea, argocd CLI
source "$(dirname "$0")/lib/common.sh"

# Use user-writable bin directory (same as where k3d installs)
USER_BIN="$HOME/bin"
mkdir -p "$USER_BIN"
export PATH="$USER_BIN:$PATH"

log_info "Checking prerequisites..."

# k3d
if ! command -v k3d &>/dev/null; then
  log_info "Installing k3d..."
  curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | USE_SUDO=false K3D_INSTALL_DIR="$USER_BIN" bash
  log_ok "k3d installed: $(k3d version 2>/dev/null | head -1)"
else
  log_ok "k3d already installed: $(k3d version 2>/dev/null | head -1)"
fi

# helm (pin to v3.17.3 - v3.20.x has a Go runtime crash bug)
HELM_WANT="v3.17.3"
if ! command -v helm &>/dev/null; then
  log_info "Installing helm ${HELM_WANT}..."
  curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | USE_SUDO=false HELM_INSTALL_DIR="$USER_BIN" DESIRED_VERSION="$HELM_WANT" bash
  log_ok "helm installed: $(helm version --short)"
else
  HELM_CUR="$(helm version --short 2>/dev/null)"
  if [[ "$HELM_CUR" == *"3.20"* ]]; then
    log_warn "Helm $HELM_CUR has a known crash bug, downgrading to ${HELM_WANT}..."
    curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | USE_SUDO=false HELM_INSTALL_DIR="$USER_BIN" DESIRED_VERSION="$HELM_WANT" bash
    log_ok "helm reinstalled: $(helm version --short)"
  else
    log_ok "helm already installed: $HELM_CUR"
  fi
fi

# argocd CLI
if ! command -v argocd &>/dev/null; then
  log_info "Installing argocd CLI..."
  ARGOCD_VERSION=$(curl -s https://api.github.com/repos/argoproj/argo-cd/releases/latest | grep tag_name | cut -d '"' -f 4)
  curl -sSL -o "$USER_BIN/argocd.exe" \
    "https://github.com/argoproj/argo-cd/releases/download/${ARGOCD_VERSION}/argocd-windows-amd64.exe"
  chmod +x "$USER_BIN/argocd.exe"
  log_ok "argocd CLI installed: $(argocd version --client --short 2>/dev/null || echo 'installed')"
else
  log_ok "argocd CLI already installed: $(argocd version --client --short 2>/dev/null || echo 'present')"
fi

# kubectl
if ! command -v kubectl &>/dev/null; then
  log_error "kubectl is required but not found. Install it from https://kubernetes.io/docs/tasks/tools/"
  exit 1
else
  log_ok "kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1)"
fi

# Add helm repos
log_info "Adding helm repositories..."
helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
helm repo add metallb https://metallb.github.io/metallb 2>/dev/null || true
helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets 2>/dev/null || true
helm repo update || log_warn "helm repo update had issues (non-fatal, continuing)"
log_ok "Helm repos configured"

log_ok "All prerequisites satisfied"
