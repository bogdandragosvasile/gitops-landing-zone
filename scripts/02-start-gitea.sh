#!/usr/bin/env bash
# Start the Gitea stack via docker-compose
source "$(dirname "$0")/lib/common.sh"

COMPOSE_DIR="$PROJECT_ROOT/docker-compose"

log_info "Starting Gitea stack..."

# Start without the runner first (needs registration token)
docker compose $COMPOSE_FILES \
  --env-file "$PROJECT_ROOT/.env" \
  up -d gitea-db gitea

# Wait for Gitea to be ready
wait_for_url "http://localhost:${GITEA_HTTP_PORT}" 120 "Gitea"

log_ok "Gitea stack is running at http://gitea.local:${GITEA_HTTP_PORT}"
