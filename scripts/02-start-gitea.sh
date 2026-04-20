#!/usr/bin/env bash
# Start the Gitea stack via docker-compose
source "$(dirname "$0")/lib/common.sh"

COMPOSE_DIR="$PROJECT_ROOT/docker-compose"

log_info "Starting Gitea stack..."

# Start DB + Gitea first (the runner needs a registration token that only
# exists after Gitea has booted — it's added in 03-configure-gitea.sh).
docker compose $COMPOSE_FILES \
  --env-file "$PROJECT_ROOT/.env" \
  up -d gitea-db gitea

# Wait for Gitea to be ready
wait_for_url "http://localhost:${GITEA_HTTP_PORT}" 120 "Gitea"

log_ok "Gitea stack is running at http://gitea.local:${GITEA_HTTP_PORT}"

# Bring up the remaining side services (vaultwarden + dnsmasq). dnsmasq is
# optional on unix hosts (the linux override drops its :53 bind), so a
# failure here is a warning, not fatal.
log_info "Starting side services (vaultwarden, dnsmasq)..."
docker compose $COMPOSE_FILES \
  --env-file "$PROJECT_ROOT/.env" \
  up -d vaultwarden dnsmasq 2>&1 | tail -5 \
  && log_ok "Side services started" \
  || log_warn "One or more side services failed (non-fatal for the platform)"
