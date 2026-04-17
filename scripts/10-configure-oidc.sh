#!/usr/bin/env bash
# Configure OIDC integration: Keycloak -> Gitea + ArgoCD
#
# The Keycloak --import-realm creates a minimal realm without the standard
# OIDC scopes (openid, profile, email). This script deletes the auto-imported
# realm and recreates it properly via the Keycloak Admin API, which ensures
# all built-in scopes are present. Then it creates the OIDC clients and a
# test user, and wires Gitea + ArgoCD to use Keycloak for SSO.
source "$(dirname "$0")/lib/common.sh"

log_info "Configuring OIDC integration..."

# Wait for Keycloak to be ready
KEYCLOAK_DISCOVER_URL="http://${KEYCLOAK_HOST}/realms/master/.well-known/openid-configuration"
wait_for_url "$KEYCLOAK_DISCOVER_URL" 300 "Keycloak"

# ---- Helper: get admin token ----
get_kc_token() {
  curl -sf -X POST "http://${KEYCLOAK_HOST}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password&client_id=admin-cli&username=${KEYCLOAK_ADMIN_USER}&password=${KEYCLOAK_ADMIN_PASSWORD}" \
    | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4
}

# ---- Recreate the realm via API (ensures built-in scopes exist) ----
log_info "Setting up Keycloak realm '${KEYCLOAK_REALM}'..."
TOKEN=$(get_kc_token)

# Delete existing realm if present (idempotent)
curl -sf -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}" 2>/dev/null || true

# Create realm (Keycloak auto-creates openid, profile, email, etc.)
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://${KEYCLOAK_HOST}/admin/realms" \
  -d "{
    \"realm\": \"${KEYCLOAK_REALM}\",
    \"enabled\": true,
    \"displayName\": \"GitOps Local Dev\",
    \"sslRequired\": \"none\",
    \"registrationAllowed\": true,
    \"loginWithEmailAllowed\": true,
    \"resetPasswordAllowed\": true
  }" 2>/dev/null
log_ok "Realm '${KEYCLOAK_REALM}' created with built-in scopes"

# Refresh token after realm change
TOKEN=$(get_kc_token)

# ---- Create Gitea OIDC client ----
log_info "Creating Gitea OIDC client..."
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients" \
  -d "{
    \"clientId\": \"${OIDC_GITEA_CLIENT_ID}\",
    \"name\": \"Gitea\",
    \"enabled\": true,
    \"clientAuthenticatorType\": \"client-secret\",
    \"secret\": \"${OIDC_GITEA_CLIENT_SECRET}\",
    \"redirectUris\": [
      \"http://gitea.local:${GITEA_HTTP_PORT}/*\",
      \"http://gitea:3000/*\",
      \"http://localhost:${GITEA_HTTP_PORT}/*\"
    ],
    \"webOrigins\": [
      \"http://gitea.local:${GITEA_HTTP_PORT}\",
      \"http://gitea:3000\",
      \"http://localhost:${GITEA_HTTP_PORT}\"
    ],
    \"standardFlowEnabled\": true,
    \"directAccessGrantsEnabled\": true,
    \"publicClient\": false,
    \"protocol\": \"openid-connect\"
  }" 2>/dev/null \
  && log_ok "Gitea client created" \
  || log_warn "Gitea client may already exist"

# ---- Create ArgoCD OIDC client ----
log_info "Creating ArgoCD OIDC client..."
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients" \
  -d "{
    \"clientId\": \"${OIDC_ARGOCD_CLIENT_ID}\",
    \"name\": \"ArgoCD\",
    \"enabled\": true,
    \"clientAuthenticatorType\": \"client-secret\",
    \"secret\": \"${OIDC_ARGOCD_CLIENT_SECRET}\",
    \"redirectUris\": [\"http://${ARGOCD_HOST}/*\"],
    \"webOrigins\": [\"http://${ARGOCD_HOST}\"],
    \"standardFlowEnabled\": true,
    \"directAccessGrantsEnabled\": true,
    \"publicClient\": false,
    \"protocol\": \"openid-connect\"
  }" 2>/dev/null \
  && log_ok "ArgoCD client created" \
  || log_warn "ArgoCD client may already exist"

# ---- Add 'groups' scope to both clients ----
log_info "Adding 'groups' scope to OIDC clients..."
TOKEN=$(get_kc_token)
GROUPS_ID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/client-scopes" \
  | grep -o '"id":"[^"]*","name":"groups"' | head -1 | grep -o '"id":"[^"]*"' | cut -d'"' -f4)

GITEA_UUID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients?clientId=${OIDC_GITEA_CLIENT_ID}" \
  | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

ARGOCD_UUID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients?clientId=${OIDC_ARGOCD_CLIENT_ID}" \
  | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

if [[ -n "$GROUPS_ID" && -n "$GITEA_UUID" && -n "$ARGOCD_UUID" ]]; then
  curl -sf -X PUT -H "Authorization: Bearer $TOKEN" \
    "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients/$GITEA_UUID/optional-client-scopes/$GROUPS_ID" 2>/dev/null
  curl -sf -X PUT -H "Authorization: Bearer $TOKEN" \
    "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/clients/$ARGOCD_UUID/optional-client-scopes/$GROUPS_ID" 2>/dev/null
  log_ok "Groups scope added to both clients"
else
  log_warn "Could not add groups scope (IDs: groups=$GROUPS_ID gitea=$GITEA_UUID argocd=$ARGOCD_UUID)"
fi

# ---- Create test user 'dev' ----
log_info "Creating test user 'dev'..."
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://${KEYCLOAK_HOST}/admin/realms/${KEYCLOAK_REALM}/users" \
  -d '{
    "username": "dev",
    "email": "dev@local.dev",
    "firstName": "Local",
    "lastName": "Developer",
    "enabled": true,
    "emailVerified": true,
    "credentials": [{"type": "password", "value": "dev", "temporary": false}]
  }' 2>/dev/null \
  && log_ok "User 'dev' created (password: dev)" \
  || log_warn "User 'dev' may already exist"

# ---- Configure Gitea OIDC provider ----
log_info "Configuring Gitea OIDC provider..."

# Remove any existing keycloak provider (idempotent)
EXISTING_ID=$(docker exec --user git gitea gitea admin auth list 2>/dev/null \
  | grep -i keycloak | awk '{print $1}')
if [[ -n "$EXISTING_ID" ]]; then
  docker exec --user git gitea gitea admin auth delete --id "$EXISTING_ID" 2>/dev/null || true
fi

# Add fresh provider (do NOT include 'openid' in scopes - Gitea adds it automatically)
docker exec --user git gitea gitea admin auth add-oauth \
  --name "keycloak" \
  --provider "openidConnect" \
  --key "${OIDC_GITEA_CLIENT_ID}" \
  --secret "${OIDC_GITEA_CLIENT_SECRET}" \
  --auto-discover-url "http://${KEYCLOAK_HOST}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
  --skip-local-2fa=true 2>/dev/null \
  && log_ok "Gitea OIDC provider configured" \
  || log_warn "Failed to add Gitea OIDC provider"

# ---- Configure ArgoCD OIDC ----
log_info "Updating ArgoCD OIDC configuration..."

kubectl -n argocd patch configmap argocd-cm --type merge -p "{
  \"data\": {
    \"url\": \"http://${ARGOCD_HOST}\",
    \"oidc.config\": \"name: Keycloak\nissuer: http://${KEYCLOAK_HOST}/realms/${KEYCLOAK_REALM}\nclientID: ${OIDC_ARGOCD_CLIENT_ID}\nclientSecret: ${OIDC_ARGOCD_CLIENT_SECRET}\nrequestedScopes:\n  - openid\n  - profile\n  - email\n\"
  }
}"

kubectl -n argocd patch configmap argocd-rbac-cm --type merge -p '{
  "data": {
    "policy.default": "role:readonly",
    "policy.csv": "g, admin, role:admin\n"
  }
}'

# Restart ArgoCD server to pick up OIDC config
kubectl rollout restart deployment/argocd-server -n argocd
wait_for_deployment "argocd" "argocd-server" 120

log_ok "OIDC integration complete"
log_info ""
log_info "=== SSO Login URLs ==="
log_info "  Gitea:    http://gitea.local:${GITEA_HTTP_PORT} (click 'Sign in with Keycloak')"
log_info "  ArgoCD:   http://${ARGOCD_HOST} (click 'LOG IN VIA KEYCLOAK')"
log_info "  Keycloak: http://${KEYCLOAK_HOST} (admin: ${KEYCLOAK_ADMIN_USER} / ${KEYCLOAK_ADMIN_PASSWORD})"
log_info "  Test user: dev / dev"
