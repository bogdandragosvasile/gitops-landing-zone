#!/usr/bin/env bash
# Create the k3d cluster on the shared Docker network
source "$(dirname "$0")/lib/common.sh"

require_cmd k3d
require_cmd kubectl

K3D_CONFIG="$PROJECT_ROOT/k3d/k3d-config.yaml"

# Check if cluster already exists
if k3d cluster list 2>/dev/null | grep -q "${K3D_CLUSTER_NAME}"; then
  log_warn "Cluster '${K3D_CLUSTER_NAME}' already exists"
  log_info "Switching kubectl context..."
  kubectl config use-context "k3d-${K3D_CLUSTER_NAME}"
else
  log_info "Creating k3d cluster '${K3D_CLUSTER_NAME}'..."
  k3d cluster create --config "$K3D_CONFIG"
  log_ok "Cluster created"
fi

# Give the API server a moment to start accepting connections
log_info "Waiting for k3d API server..."
ELAPSED=0
while [[ $ELAPSED -lt 60 ]]; do
  if kubectl cluster-info &>/dev/null; then
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done

# Wait for nodes to be ready
log_info "Waiting for cluster nodes..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s
log_ok "All nodes ready"

# Wait for core system pods
wait_for_pods "kube-system" 180

# Verify Traefik is running
log_info "Verifying Traefik ingress controller..."
kubectl wait --for=condition=available deployment/traefik \
  -n kube-system --timeout=120s 2>/dev/null \
  || log_warn "Traefik deployment not found as expected name, checking alternatives..."

# Add hostPort to Traefik so k3d loadbalancer port mapping (80/443) works.
# k3d maps host ports to node ports via its nginx proxy, but with servicelb
# disabled (for MetalLB), nothing binds port 80/443 on the nodes. hostPort fixes this.
log_info "Patching Traefik with hostPort for ports 80 and 443..."
kubectl patch deployment traefik -n kube-system --type json -p '[
  {"op":"add","path":"/spec/template/spec/containers/0/ports/2/hostPort","value":80},
  {"op":"add","path":"/spec/template/spec/containers/0/ports/3/hostPort","value":443}
]' 2>/dev/null \
  && log_ok "Traefik patched with hostPort" \
  || log_warn "Traefik hostPort patch failed (may already be set)"
kubectl rollout status deployment/traefik -n kube-system --timeout=60s 2>/dev/null || true

# Add local hostnames to CoreDNS so in-cluster services can resolve
# keycloak.local / argocd.local / gitea.local to the Traefik LB IP.
log_info "Patching CoreDNS with local hostname resolution..."
CURRENT_HOSTS=$(kubectl get configmap coredns -n kube-system -o jsonpath='{.data.NodeHosts}')
if echo "$CURRENT_HOSTS" | grep -q "keycloak.local"; then
  log_ok "CoreDNS already has local hostnames"
else
  NEW_HOSTS="${CURRENT_HOSTS}
${METALLB_IP_START} keycloak.local
${METALLB_IP_START} argocd.local
${METALLB_IP_START} gitea.local
"
  # Write patch file to avoid shell escaping issues
  PATCH_FILE="/tmp/coredns-patch.yaml"
  {
    echo "data:"
    echo "  NodeHosts: |"
    echo "$NEW_HOSTS" | sed 's/^/    /'
  } > "$PATCH_FILE"
  kubectl patch configmap coredns -n kube-system --patch-file "$PATCH_FILE" 2>/dev/null
  rm -f "$PATCH_FILE"
  kubectl rollout restart deployment coredns -n kube-system 2>/dev/null
  kubectl wait --for=condition=available deployment/coredns -n kube-system --timeout=60s 2>/dev/null
  log_ok "CoreDNS patched with local hostnames -> ${METALLB_IP_START}"
fi

# Show cluster info
log_ok "k3d cluster '${K3D_CLUSTER_NAME}' is ready"
kubectl cluster-info
