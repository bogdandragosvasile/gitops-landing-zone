#!/usr/bin/env bash
# Initialize and push the gitops-repo to Gitea
source "$(dirname "$0")/lib/common.sh"

GITOPS_DIR="$PROJECT_ROOT/gitops-repo"

log_info "Preparing gitops-infra repository..."

# Process templates - substitute only known env vars (preserves $values etc.)
ENVSUBST_VARS='${GITEA_ORG} ${GITEA_REPO} ${GITEA_ADMIN_USER} ${GITEA_ADMIN_PASSWORD} ${GITEA_ADMIN_EMAIL} ${GITEA_HTTP_PORT} ${GITEA_SSH_PORT} ${GITEA_DB_USER} ${GITEA_DB_PASSWORD} ${GITEA_DB_NAME} ${GITEA_URL} ${GITEA_EXTERNAL_URL} ${ARGOCD_HOST} ${ARGOCD_ADMIN_PASSWORD} ${KEYCLOAK_HOST} ${KEYCLOAK_ADMIN_USER} ${KEYCLOAK_ADMIN_PASSWORD} ${KEYCLOAK_DB_USER} ${KEYCLOAK_DB_PASSWORD} ${KEYCLOAK_DB_NAME} ${KEYCLOAK_REALM} ${OIDC_GITEA_CLIENT_ID} ${OIDC_GITEA_CLIENT_SECRET} ${OIDC_ARGOCD_CLIENT_ID} ${OIDC_ARGOCD_CLIENT_SECRET} ${METALLB_IP_START} ${METALLB_IP_END} ${BASE_IP} ${DOMAIN_SUFFIX}'

log_info "Processing templates with environment variables..."
for f in $(find "$GITOPS_DIR" -name '*.yaml' -o -name '*.json' | sort); do
  if grep -q '\${' "$f" 2>/dev/null; then
    log_info "  Processing: $(basename "$f")"
    envsubst "$ENVSUBST_VARS" < "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
  fi
done

# Initialize git repo
cd "$GITOPS_DIR"
if [[ ! -d .git ]]; then
  git init -b main
  git config user.name "GitOps Bootstrap"
  git config user.email "bootstrap@local.dev"
fi

git add -A
git commit -m "Initial gitops-infra: app-of-apps with ArgoCD, Keycloak, MetalLB, cert-manager, sealed-secrets" 2>/dev/null \
  || log_warn "Nothing to commit (already up to date)"

# Configure remote and push
REPO_URL="http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:${GITEA_HTTP_PORT}/${GITEA_ORG}/${GITEA_REPO}.git"

git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

log_info "Pushing to Gitea: ${GITEA_ORG}/${GITEA_REPO}..."
git push -u origin main --force

cd "$PROJECT_ROOT"
log_ok "gitops-infra repo pushed to Gitea"
