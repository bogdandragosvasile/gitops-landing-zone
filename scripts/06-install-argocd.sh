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

# Set admin password by patching argocd-secret directly with a bcrypt hash.
# This bypasses the CLI login flow (which tends to fail on fresh installs
# because the ingress path and certs aren't ready yet) and is fully offline.
log_info "Setting ArgoCD admin password (bcrypt, offline)..."

# bcrypt the desired password. Prefer htpasswd when available; fall back to
# python3's bcrypt module (auto-install into the user site-packages if absent).
BCRYPT_HASH=""
if command -v htpasswd &>/dev/null; then
  BCRYPT_HASH=$(htpasswd -nbBC 10 "" "${ARGOCD_ADMIN_PASSWORD}" 2>/dev/null | tr -d ':\n' | sed 's/^\$2y\$/\$2a\$/')
fi
if [[ -z "$BCRYPT_HASH" ]]; then
  # python3 + bcrypt fallback. Install into user site silently if missing.
  python3 -c 'import bcrypt' 2>/dev/null || pip3 install --user --quiet bcrypt 2>/dev/null || true
  BCRYPT_HASH=$(PW="${ARGOCD_ADMIN_PASSWORD}" python3 -c '
import os, sys
try:
    import bcrypt
    print(bcrypt.hashpw(os.environ["PW"].encode(), bcrypt.gensalt(rounds=10)).decode())
except Exception as e:
    sys.exit(1)
' 2>/dev/null || true)
fi

if [[ -n "$BCRYPT_HASH" ]]; then
  # Encode hash + current timestamp, patch argocd-secret.
  B64_HASH=$(printf '%s' "$BCRYPT_HASH" | base64 | tr -d '\n')
  B64_MTIME=$(date -u +%FT%TZ | base64 | tr -d '\n')
  kubectl -n argocd patch secret argocd-secret \
    --type merge \
    -p "{\"data\":{\"admin.password\":\"${B64_HASH}\",\"admin.passwordMtime\":\"${B64_MTIME}\"}}" \
    >/dev/null 2>&1 \
    && log_ok "ArgoCD admin password set via argocd-secret patch" \
    || log_warn "Failed to patch argocd-secret — ArgoCD may still use initial password"
  # Restart argocd-server so it reloads the secret immediately.
  kubectl -n argocd rollout restart deploy argocd-server 2>/dev/null
  kubectl -n argocd rollout status deploy argocd-server --timeout=90s >/dev/null 2>&1 || true
  kubectl -n argocd delete secret argocd-initial-admin-secret --ignore-not-found >/dev/null 2>&1 || true
else
  log_warn "Could not produce bcrypt hash — leaving initial admin password in place."
  log_warn "  Initial password: $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)"
fi

log_ok "ArgoCD is ready"
log_info "  URL: http://${ARGOCD_HOST}"
log_info "  Admin: admin / ${ARGOCD_ADMIN_PASSWORD}"
