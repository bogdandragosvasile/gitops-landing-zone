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

# ---- Wait for the Keycloak pod to be up and serving the master realm ----
# The PostSync hook races with keycloak startup; make sure keycloak itself
# responds before we try to provision anything.
log_info "Waiting for Keycloak pod readiness..."
kubectl wait --for=condition=Ready pod/keycloak-keycloakx-0 -n keycloak --timeout=600s \
  >/dev/null 2>&1 \
  || log_warn "keycloak-keycloakx-0 did not reach Ready in 600s"

# ---- Run (or re-run) the keycloak-configure PostSync hook ourselves ----
# Rendering is done via the exact ENVSUBST_VARS whitelist used by
# 07-push-gitops-repo.sh — running plain `envsubst` on this file without a
# whitelist would clobber the in-script shell vars ($KC_URL, $KC_ADMIN_USER
# etc.), producing a script that curls an empty URL and hangs forever.
# The PostSync hook annotation on the ConfigMap/Job becomes harmless on a
# direct apply; ArgoCD will still own these resources via tracking labels.
ENVSUBST_VARS='${GITEA_ORG} ${GITEA_REPO} ${GITEA_ADMIN_USER} ${GITEA_ADMIN_PASSWORD} ${GITEA_ADMIN_EMAIL} ${GITEA_HTTP_PORT} ${GITEA_SSH_PORT} ${GITEA_DB_USER} ${GITEA_DB_PASSWORD} ${GITEA_DB_NAME} ${GITEA_URL} ${GITEA_EXTERNAL_URL} ${ARGOCD_HOST} ${ARGOCD_URL} ${ARGOCD_ADMIN_PASSWORD} ${KEYCLOAK_HOST} ${KEYCLOAK_ADMIN_USER} ${KEYCLOAK_ADMIN_PASSWORD} ${KEYCLOAK_DB_USER} ${KEYCLOAK_DB_PASSWORD} ${KEYCLOAK_DB_NAME} ${KEYCLOAK_REALM} ${OIDC_GITEA_CLIENT_ID} ${OIDC_GITEA_CLIENT_SECRET} ${OIDC_ARGOCD_CLIENT_ID} ${OIDC_ARGOCD_CLIENT_SECRET} ${GRAFANA_ADMIN_PASSWORD} ${METALLB_IP_START} ${METALLB_IP_END} ${BASE_IP} ${DOMAIN_SUFFIX}'

RENDERED_JOB="$(mktemp -t keycloak-configure.XXXXXX).yaml"
trap 'rm -f "$RENDERED_JOB"' EXIT
envsubst "$ENVSUBST_VARS" \
  < "$PROJECT_ROOT/gitops-repo/manifests/keycloak/configure-job.yaml" \
  > "$RENDERED_JOB"

log_info "Running Keycloak realm / client / user bootstrap Job..."
kubectl delete job keycloak-configure -n keycloak --ignore-not-found >/dev/null 2>&1 || true
kubectl apply -n keycloak -f "$RENDERED_JOB" >/dev/null

# Wait for the Job to finish — Completed means realm + clients + dev user ready.
if kubectl wait --for=condition=Complete job/keycloak-configure -n keycloak --timeout=300s \
    >/dev/null 2>&1; then
  log_ok "Keycloak bootstrap Job completed"
  kubectl logs -n keycloak -l job-name=keycloak-configure --tail=20 2>/dev/null \
    | grep -E '\[INFO\]|\[ OK\]' || true
else
  log_warn "Keycloak bootstrap Job did not complete in 300s — full logs:"
  kubectl logs -n keycloak -l job-name=keycloak-configure --tail=40 2>/dev/null || true
fi

# Sanity probe — hit the realm from within the keycloak pod itself (no TTY
# games, no extra pod to schedule). The keycloakx image has curl bundled.
STATUS=$(kubectl exec -n keycloak keycloak-keycloakx-0 -- \
  curl -s -o /dev/null -w "%{http_code}" \
  "http://keycloak-keycloakx-http.keycloak.svc.cluster.local/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
  2>/dev/null || echo "000")
if [[ "$STATUS" == "200" ]]; then
  log_ok "Realm '${KEYCLOAK_REALM}' is live (in-cluster probe)"
else
  log_warn "Realm '${KEYCLOAK_REALM}' probe returned '$STATUS' (expected 200)"
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
