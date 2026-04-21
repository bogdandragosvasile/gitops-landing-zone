#!/usr/bin/env bash
# Create the k3d cluster on the shared Docker network
source "$(dirname "$0")/lib/common.sh"

require_cmd k3d
require_cmd kubectl

K3D_CONFIG="$PROJECT_ROOT/k3d/k3d-config.yaml"

# Check if cluster already exists
if k3d cluster list 2>/dev/null | grep -q "${K3D_CLUSTER_NAME}"; then
  log_warn "Cluster '${K3D_CLUSTER_NAME}' already exists"
else
  log_info "Creating k3d cluster '${K3D_CLUSTER_NAME}'..."
  k3d cluster create --config "$K3D_CONFIG"
  log_ok "Cluster created"
fi

# Merge kubeconfig and switch context (works whether cluster was just created or pre-existing)
log_info "Merging kubeconfig and switching context to k3d-${K3D_CLUSTER_NAME}..."
unset KUBECONFIG
k3d kubeconfig merge "${K3D_CLUSTER_NAME}" --kubeconfig-merge-default --kubeconfig-switch-context
log_ok "kubectl context: $(kubectl config current-context)"

# Patch k3d node DNS.
# On macOS + Colima the default nameserver in k3d nodes (e.g. 192.168.5.2,
# Colima's internal resolver) is unreachable from within the nested `gitops`
# Docker network — image pulls fail with "dial tcp: lookup <host>: Try again".
# Docker-generated /etc/resolv.conf is marked as user-editable, so patching
# it directly is persistent until the node is re-created.
if [[ "$PLATFORM" == "macos" ]]; then
  log_info "Patching k3d node DNS for Colima (public resolvers)..."
  # k3d 5.8.x has no `--cluster` flag; filter by the runtime label and
  # keep only server/agent nodes (loadbalancer + tools lack containerd).
  for node in $(k3d node list -o json 2>/dev/null \
      | python3 -c "import sys,json
for n in json.load(sys.stdin):
    if n.get('role') not in ('server','agent'): continue
    if n.get('runtimeLabels',{}).get('k3d.cluster') != '${K3D_CLUSTER_NAME}': continue
    print(n['name'])" 2>/dev/null); do
    docker exec "$node" sh -c 'printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions ndots:0\n" > /etc/resolv.conf' \
      && log_info "  → $node DNS patched" \
      || log_warn "  → $node DNS patch failed"
  done
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
${METALLB_IP_START} portal.local
${METALLB_IP_START} grafana.local
${METALLB_IP_START} prometheus.local
${METALLB_IP_START} industry40.local
${METALLB_IP_START} headscale.local
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
