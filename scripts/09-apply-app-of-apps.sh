#!/usr/bin/env bash
# Apply the root Application to kick off app-of-apps
source "$(dirname "$0")/lib/common.sh"

APPS_DIR="$PROJECT_ROOT/gitops-repo/apps"

log_info "Applying root app-of-apps Application..."

envsubst < "$APPS_DIR/root-app.yaml" | kubectl apply -f -

log_ok "Root Application applied"
log_info "ArgoCD will now sync all child applications..."

# Wait for applications to appear
log_info "Waiting for applications to register..."
sleep 10

# List all applications
log_info "Current ArgoCD Applications:"
kubectl get applications -n argocd 2>/dev/null || true

# Wait for critical applications
log_info "Waiting for cert-manager..."
kubectl wait --for=jsonpath='{.status.health.status}'=Healthy \
  application/cert-manager -n argocd --timeout=300s 2>/dev/null \
  || log_warn "cert-manager not healthy yet (may still be syncing)"

log_info "Waiting for sealed-secrets..."
kubectl wait --for=jsonpath='{.status.health.status}'=Healthy \
  application/sealed-secrets -n argocd --timeout=300s 2>/dev/null \
  || log_warn "sealed-secrets not healthy yet"

log_info "Waiting for keycloak-postgres..."
kubectl wait --for=jsonpath='{.status.health.status}'=Healthy \
  application/keycloak-postgres -n argocd --timeout=300s 2>/dev/null \
  || log_warn "keycloak-postgres not healthy yet"

# Keycloak may need a manual sync on first deploy (auto-sync can stall on
# chart switches). Force-sync via argocd CLI using port-forward.
log_info "Waiting for keycloak to sync..."
ELAPSED=0
while [[ $ELAPSED -lt 60 ]]; do
  KC_SYNC=$(kubectl get application keycloak -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null)
  KC_HEALTH=$(kubectl get application keycloak -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null)
  if [[ "$KC_SYNC" == "Synced" && "$KC_HEALTH" != "Missing" ]]; then
    break
  fi
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

if [[ "$KC_SYNC" != "Synced" || "$KC_HEALTH" == "Missing" ]]; then
  log_info "Force-syncing keycloak via argocd CLI..."
  INITIAL_PW=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d)
  if [[ -z "$INITIAL_PW" ]]; then
    INITIAL_PW="${ARGOCD_ADMIN_PASSWORD}"
  fi
  kubectl port-forward svc/argocd-server -n argocd 18080:80 &>/dev/null &
  PF_PID=$!
  sleep 3
  yes | argocd login localhost:18080 --insecure --plaintext --username admin --password "$INITIAL_PW" 2>/dev/null || true
  argocd app terminate-op keycloak 2>/dev/null || true
  sleep 2
  argocd app sync keycloak --force 2>/dev/null || true
  kill $PF_PID 2>/dev/null || true
fi

log_info "Waiting for keycloak pod to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=keycloakx \
  -n keycloak --timeout=300s 2>/dev/null \
  || kubectl wait --for=jsonpath='{.status.health.status}'=Healthy \
    application/keycloak -n argocd --timeout=600s 2>/dev/null \
  || log_warn "keycloak not fully healthy yet"

log_ok "App-of-apps deployment initiated"
log_info "Run 'kubectl get applications -n argocd' to monitor progress"
