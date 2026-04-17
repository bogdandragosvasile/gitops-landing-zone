#!/usr/bin/env bash
# Bootstrap ArgoCD (initial install without OIDC)
source "$(dirname "$0")/lib/common.sh"

require_cmd helm
require_cmd kubectl

ARGOCD_VALUES_TEMPLATE="$PROJECT_ROOT/gitops-repo/manifests/argocd/values.yaml"
ARGOCD_VALUES_RENDERED="/tmp/argocd-values-rendered.yaml"

log_info "Installing ArgoCD..."

# Render the values template with env vars
envsubst < "$ARGOCD_VALUES_TEMPLATE" > "$ARGOCD_VALUES_RENDERED"

kubectl create namespace argocd 2>/dev/null || true

# Check if already installed
if helm list -n argocd 2>/dev/null | grep -q argocd; then
  log_warn "ArgoCD already installed, upgrading..."
  helm upgrade argocd argo/argo-cd \
    -n argocd \
    --version 9.4.17 \
    -f "$ARGOCD_VALUES_RENDERED" \
    --wait --timeout 180s
else
  helm install argocd argo/argo-cd \
    -n argocd \
    --version 9.4.17 \
    -f "$ARGOCD_VALUES_RENDERED" \
    --wait --timeout 180s
fi

rm -f "$ARGOCD_VALUES_RENDERED"

log_ok "ArgoCD installed"

# Wait for ArgoCD server
wait_for_deployment "argocd" "argocd-server" 180

# Set admin password via port-forward + argocd CLI
log_info "Setting ArgoCD admin password..."
INITIAL_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" 2>/dev/null | base64 -d)

if [[ -n "$INITIAL_PASSWORD" ]]; then
  # Port-forward is the most reliable way to reach argocd-server from the host
  kubectl port-forward svc/argocd-server -n argocd 18080:80 &>/dev/null &
  PF_PID=$!
  sleep 3

  if yes | argocd login localhost:18080 --insecure --plaintext \
    --username admin --password "$INITIAL_PASSWORD" 2>/dev/null; then
    argocd account update-password \
      --current-password "$INITIAL_PASSWORD" \
      --new-password "${ARGOCD_ADMIN_PASSWORD}" 2>/dev/null \
      && log_ok "Admin password updated" \
      || log_warn "Could not update password, using initial password"
    kubectl -n argocd delete secret argocd-initial-admin-secret 2>/dev/null || true
  else
    log_warn "Could not login to ArgoCD CLI. Initial password: $INITIAL_PASSWORD"
  fi

  kill $PF_PID 2>/dev/null || true
  wait $PF_PID 2>/dev/null || true
else
  log_warn "No initial admin secret found. ArgoCD may already be configured."
fi

log_ok "ArgoCD is ready"
log_info "  URL: http://${ARGOCD_HOST}"
log_info "  Admin: admin / ${ARGOCD_ADMIN_PASSWORD}"
