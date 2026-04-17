#!/usr/bin/env bash
# Configure Gitea: admin user, organization, runner token
source "$(dirname "$0")/lib/common.sh"

COMPOSE_DIR="$PROJECT_ROOT/docker-compose"

log_info "Configuring Gitea..."

# Wait for Gitea API to be ready
log_info "Waiting for Gitea API..."
ELAPSED=0
while [[ $ELAPSED -lt 90 ]]; do
  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "http://localhost:${GITEA_HTTP_PORT}/api/v1/version" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" == "200" ]]; then
    log_ok "Gitea API is ready (${ELAPSED}s)"
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done
if [[ "$HTTP_CODE" != "200" ]]; then
  log_error "Gitea API not ready (HTTP $HTTP_CODE). Check if INSTALL_LOCK is set."
  exit 1
fi

# Create admin user via CLI running as the 'git' user (not root)
log_info "Creating admin user '${GITEA_ADMIN_USER}'..."
docker exec --user git gitea gitea admin user create \
  --admin \
  --username "${GITEA_ADMIN_USER}" \
  --password "${GITEA_ADMIN_PASSWORD}" \
  --email "${GITEA_ADMIN_EMAIL}" \
  --must-change-password=false 2>&1 \
  && log_ok "Admin user created" \
  || log_warn "Admin user may already exist (this is OK)"

# Create API token for automation
log_info "Creating API token..."
TOKEN_RESPONSE=$(curl -sf -X POST \
  "http://localhost:${GITEA_HTTP_PORT}/api/v1/users/${GITEA_ADMIN_USER}/tokens" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"name":"bootstrap-token","scopes":["all"]}' 2>/dev/null || true)

if [[ -n "$TOKEN_RESPONSE" ]]; then
  GITEA_API_TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)
  if [[ -z "$GITEA_API_TOKEN" ]]; then
    GITEA_API_TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
  fi
  log_ok "API token created"
else
  log_warn "Could not create API token (may already exist), using basic auth"
fi

# Create organization
log_info "Creating organization '${GITEA_ORG}'..."
curl -sf -X POST \
  "http://localhost:${GITEA_HTTP_PORT}/api/v1/orgs" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${GITEA_ORG}\",\"visibility\":\"public\"}" 2>/dev/null \
  && log_ok "Organization '${GITEA_ORG}' created" \
  || log_warn "Organization may already exist (this is OK)"

# Create the gitops-infra repository
log_info "Creating repository '${GITEA_ORG}/${GITEA_REPO}'..."
curl -sf -X POST \
  "http://localhost:${GITEA_HTTP_PORT}/api/v1/orgs/${GITEA_ORG}/repos" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${GITEA_REPO}\",\"auto_init\":false,\"default_branch\":\"main\"}" 2>/dev/null \
  && log_ok "Repository created" \
  || log_warn "Repository may already exist (this is OK)"

# Get runner registration token (run as git user, extract last line which is the actual token)
log_info "Fetching runner registration token..."
RUNNER_TOKEN_RAW=$(docker exec --user git gitea gitea actions generate-runner-token 2>&1 || true)
RUNNER_TOKEN=$(echo "$RUNNER_TOKEN_RAW" | tail -1 | tr -d '[:space:]')

if [[ -z "$RUNNER_TOKEN" ]] || echo "$RUNNER_TOKEN" | grep -qi "error\|fatal\|not supposed"; then
  # Fallback: try via Gitea API
  log_info "Trying runner token via API..."
  RUNNER_TOKEN=$(curl -sf -X POST \
    "http://localhost:${GITEA_HTTP_PORT}/api/v1/user/actions/runners/registration-token" \
    -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" 2>/dev/null | grep -o '"token":"[^"]*"' | cut -d'"' -f4 || true)
fi

if [[ -n "$RUNNER_TOKEN" ]] && ! echo "$RUNNER_TOKEN" | grep -qi "error\|fatal"; then
  # Update .env with the real runner token (use python/awk to avoid sed delimiter issues)
  awk -v tok="$RUNNER_TOKEN" '/^GITEA_RUNNER_TOKEN=/{$0="GITEA_RUNNER_TOKEN="tok}1' "$PROJECT_ROOT/.env" > "$PROJECT_ROOT/.env.tmp" \
    && mv "$PROJECT_ROOT/.env.tmp" "$PROJECT_ROOT/.env"
  log_ok "Runner token saved to .env"

  # Remove stale runner container if any, then start fresh
  docker rm -f gitea-runner 2>/dev/null || true

  log_info "Starting Gitea runner..."
  export GITEA_RUNNER_TOKEN="$RUNNER_TOKEN"
  docker compose $COMPOSE_FILES \
    --env-file "$PROJECT_ROOT/.env" \
    up -d gitea-runner
  log_ok "Gitea runner started"
else
  log_warn "Could not generate runner token. Start runner manually after setting GITEA_RUNNER_TOKEN in .env"
fi

log_ok "Gitea configuration complete"
log_info "  URL: http://gitea.local:${GITEA_HTTP_PORT}"
log_info "  Admin: ${GITEA_ADMIN_USER} / ${GITEA_ADMIN_PASSWORD}"
log_info "  Org: ${GITEA_ORG}"
