#!/usr/bin/env bash
# Render the gitops-repo templates into a temp directory, then initialise
# + push that rendered copy to Gitea. Never mutates the source tree in this
# repo — so secrets stay out of the landing-zone git history. The source
# files under gitops-repo/ are templates with ${VAR} placeholders, and
# envsubst resolves them here.
source "$(dirname "$0")/lib/common.sh"

require_cmd envsubst

SRC_DIR="$PROJECT_ROOT/gitops-repo"
RENDER_DIR="$(mktemp -d -t gitops-infra-render.XXXXXX)"
trap 'rm -rf "$RENDER_DIR"' EXIT

log_info "Rendering templates into $RENDER_DIR ..."

# The envsubst list scopes the substitution to known vars, preserving any
# other literal $-references (e.g. Helm $values).
ENVSUBST_VARS='${GITEA_ORG} ${GITEA_REPO} ${GITEA_ADMIN_USER} ${GITEA_ADMIN_PASSWORD} ${GITEA_ADMIN_EMAIL} ${GITEA_HTTP_PORT} ${GITEA_SSH_PORT} ${GITEA_DB_USER} ${GITEA_DB_PASSWORD} ${GITEA_DB_NAME} ${GITEA_URL} ${GITEA_EXTERNAL_URL} ${ARGOCD_HOST} ${ARGOCD_URL} ${ARGOCD_ADMIN_PASSWORD} ${KEYCLOAK_HOST} ${KEYCLOAK_ADMIN_USER} ${KEYCLOAK_ADMIN_PASSWORD} ${KEYCLOAK_DB_USER} ${KEYCLOAK_DB_PASSWORD} ${KEYCLOAK_DB_NAME} ${KEYCLOAK_REALM} ${OIDC_GITEA_CLIENT_ID} ${OIDC_GITEA_CLIENT_SECRET} ${OIDC_ARGOCD_CLIENT_ID} ${OIDC_ARGOCD_CLIENT_SECRET} ${METALLB_IP_START} ${METALLB_IP_END} ${BASE_IP} ${DOMAIN_SUFFIX}'

# Copy entire source tree preserving structure, then envsubst in place
# inside the temp directory only (source stays untouched).
( cd "$SRC_DIR" && tar cf - . ) | ( cd "$RENDER_DIR" && tar xf - )

while IFS= read -r f; do
  if grep -q '\${' "$f" 2>/dev/null; then
    envsubst "$ENVSUBST_VARS" < "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
  fi
done < <(find "$RENDER_DIR" \( -name '*.yaml' -o -name '*.json' \) | sort)

log_ok "Templates rendered"

# Initialize the rendered tree as a fresh git repo and push to Gitea.
cd "$RENDER_DIR"
git init -q -b main
git config user.name "GitOps Bootstrap"
git config user.email "bootstrap@local.dev"
git add -A
git -c commit.gpgsign=false commit -q -m "Rendered gitops-infra — $(date -u +%FT%TZ)" \
  || log_warn "Nothing to commit (rendered tree is empty?)"

REPO_URL="http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:${GITEA_HTTP_PORT}/${GITEA_ORG}/${GITEA_REPO}.git"
git remote add origin "$REPO_URL" 2>/dev/null

log_info "Pushing to Gitea: ${GITEA_ORG}/${GITEA_REPO}..."
git push -u origin main --force 2>&1 | tail -3

cd "$PROJECT_ROOT"
log_ok "gitops-infra repo rendered + pushed to Gitea (source tree untouched)"
