#!/usr/bin/env bash
# Phase 10: Wire up Gitea OIDC provider to Keycloak.
#
# What is now GitOps-managed (no longer in this script):
#   - Keycloak realm / clients / groups scope / dev user  →  PostSync Job in keycloak namespace
#   - ArgoCD OIDC config (oidc.config in argocd-cm)       →  gitops-repo/manifests/argocd/values.yaml
#   - ArgoCD RBAC policy                                   →  gitops-repo/manifests/argocd/values.yaml
#
# What remains here (legitimately imperative — Gitea runs in Docker Compose, not k8s):
#   - Gitea OAuth2 authentication source  →  docker exec gitea admin auth add-oauth
source "$(dirname "$0")/lib/common.sh"

log_info "Configuring OIDC integration..."

# ---- Wait for Keycloak realm to be configured ----
# The PostSync Job (keycloak-configure) creates the gitops realm after ArgoCD syncs.
# The Job is deleted by ArgoCD on success (HookSucceeded policy) — we can't poll it
# directly. Instead, poll for the realm to appear via the Admin API.
log_info "Waiting for Keycloak 'gitops' realm to be ready (PostSync Job may still be running)..."
WAITED=0
MAX_WAIT=300
REALM_READY=0
while [[ $WAITED -lt $MAX_WAIT ]]; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://${KEYCLOAK_HOST}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" 2>/dev/null || true)
  if [[ "$STATUS" == "200" ]]; then
    log_ok "Keycloak realm '${KEYCLOAK_REALM}' is ready (${WAITED}s)"
    REALM_READY=1
    break
  fi
  # If the Job failed, it will have left a failed pod — surface this as a warning
  FAILED_JOB=$(kubectl get job keycloak-configure -n keycloak \
    -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || true)
  if [[ "$FAILED_JOB" == "True" ]]; then
    log_warn "keycloak-configure job failed — check: kubectl logs -n keycloak -l app=keycloak-configure"
    break
  fi
  sleep 10
  WAITED=$((WAITED + 10))
done
if [[ "$REALM_READY" -eq 0 ]]; then
  log_error "Keycloak realm '${KEYCLOAK_REALM}' not ready after ${MAX_WAIT}s. Continuing anyway..."
fi

# ---- Configure Gitea OIDC provider ----
# Gitea's auth source management is CLI-only (no REST API for OAuth2 sources).
# Gitea runs in Docker Compose on the host, so we must use docker exec.
log_info "Configuring Gitea OIDC provider..."

EXISTING_ID=$(docker exec --user git gitea gitea admin auth list 2>/dev/null \
  | grep -i keycloak | awk '{print $1}' || true)
if [[ -n "$EXISTING_ID" ]]; then
  docker exec --user git gitea gitea admin auth delete --id "$EXISTING_ID" 2>/dev/null || true
fi

docker exec --user git gitea gitea admin auth add-oauth \
  --name "keycloak" \
  --provider "openidConnect" \
  --key "${OIDC_GITEA_CLIENT_ID}" \
  --secret "${OIDC_GITEA_CLIENT_SECRET}" \
  --auto-discover-url "http://${KEYCLOAK_HOST}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
  --skip-local-2fa \
  && log_ok "Gitea OIDC provider configured" \
  || log_warn "Failed to add Gitea OIDC provider (may already be configured)"

log_ok "OIDC integration complete"
log_info ""
log_info "=== SSO Login URLs ==="
log_info "  Gitea:    http://gitea.local:${GITEA_HTTP_PORT}  — click 'Sign in with Keycloak'"
log_info "  ArgoCD:   http://${ARGOCD_HOST}                  — click 'LOG IN VIA KEYCLOAK'"
log_info "  Keycloak: http://${KEYCLOAK_HOST}                — admin: ${KEYCLOAK_ADMIN_USER}"
log_info "  Test user: dev / dev"
