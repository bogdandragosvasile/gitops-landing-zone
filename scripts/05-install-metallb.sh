#!/usr/bin/env bash
# Install MetalLB and configure the IP address pool
source "$(dirname "$0")/lib/common.sh"

require_cmd helm
require_cmd kubectl

MANIFESTS_DIR="$PROJECT_ROOT/gitops-repo/manifests/metallb"

log_info "Installing MetalLB..."

# Check if already installed
if helm list -n metallb-system 2>/dev/null | grep -q metallb; then
  log_warn "MetalLB already installed, upgrading..."
  helm upgrade metallb metallb/metallb \
    -n metallb-system \
    --version 0.14.9 \
    --wait
else
  kubectl create namespace metallb-system 2>/dev/null || true
  helm install metallb metallb/metallb \
    -n metallb-system \
    --version 0.14.9 \
    --wait --timeout 120s
fi

log_ok "MetalLB controller installed"

# Wait for the controller and speaker to be ready
wait_for_deployment "metallb-system" "metallb-controller" 120

# Small delay for CRDs to register
sleep 5

# Apply IP address pool and L2 advertisement (envsubst the templates inline)
log_info "Configuring MetalLB IP pool: ${METALLB_IP_START} - ${METALLB_IP_END}"
envsubst < "$MANIFESTS_DIR/ipaddresspool.yaml" | kubectl apply -f -
envsubst < "$MANIFESTS_DIR/l2advertisement.yaml" | kubectl apply -f -

log_ok "MetalLB configured with IP pool ${METALLB_IP_START}-${METALLB_IP_END}"
