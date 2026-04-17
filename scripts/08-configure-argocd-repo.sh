#!/usr/bin/env bash
# Register the Gitea repository in ArgoCD
source "$(dirname "$0")/lib/common.sh"

log_info "Registering Gitea repository in ArgoCD..."

# Try using argocd CLI
if command -v argocd &>/dev/null; then
  log_info "Logging into ArgoCD..."
  argocd login "${ARGOCD_HOST}" \
    --username admin \
    --password "${ARGOCD_ADMIN_PASSWORD}" \
    --insecure \
    --grpc-web 2>/dev/null \
    && log_ok "ArgoCD CLI login successful" \
    || log_warn "ArgoCD CLI login failed, falling back to kubectl"

  argocd repo add "http://gitea:3000/${GITEA_ORG}/${GITEA_REPO}.git" \
    --username "${GITEA_ADMIN_USER}" \
    --password "${GITEA_ADMIN_PASSWORD}" \
    --insecure-skip-server-verification 2>/dev/null \
    && log_ok "Repository registered via CLI" \
    || log_warn "CLI repo add failed, trying kubectl method"
fi

# Fallback: create repo secret directly via kubectl
log_info "Ensuring repository secret exists via kubectl..."
kubectl apply -n argocd -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: gitea-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  type: git
  url: "http://gitea:3000/${GITEA_ORG}/${GITEA_REPO}.git"
  username: "${GITEA_ADMIN_USER}"
  password: "${GITEA_ADMIN_PASSWORD}"
EOF

log_ok "Gitea repository registered in ArgoCD"

# Restart repo-server so it picks up the new secret immediately instead of
# waiting for its reconcile cycle (which can cache a stale auth failure).
log_info "Restarting argocd-repo-server to load new credentials..."
kubectl rollout restart deployment argocd-repo-server -n argocd 2>/dev/null
kubectl rollout status deployment argocd-repo-server -n argocd --timeout=90s 2>/dev/null \
  && log_ok "argocd-repo-server restarted" \
  || log_warn "argocd-repo-server rollout timed out (non-fatal)"
