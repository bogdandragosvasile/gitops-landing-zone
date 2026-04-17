#!/usr/bin/env bash
# Teardown the entire local GitOps environment
set -euo pipefail

source "$(dirname "$0")/lib/common.sh"

echo ""
echo "=============================================="
echo "  Local GitOps Landing Zone - Teardown"
echo "=============================================="
echo ""

# Delete k3d cluster
log_info "Deleting k3d cluster '${K3D_CLUSTER_NAME}'..."
k3d cluster delete "${K3D_CLUSTER_NAME}" 2>/dev/null \
  && log_ok "Cluster deleted" \
  || log_warn "Cluster not found or already deleted"

# Stop and remove docker-compose services
log_info "Stopping Gitea stack..."
docker compose -f "$PROJECT_ROOT/docker-compose/docker-compose.yml" \
  --env-file "$PROJECT_ROOT/.env" \
  down -v 2>/dev/null \
  && log_ok "Gitea stack removed" \
  || log_warn "Gitea stack not found or already removed"

# Remove docker network
log_info "Removing Docker network '${DOCKER_NETWORK}'..."
docker network rm "${DOCKER_NETWORK}" 2>/dev/null \
  && log_ok "Network removed" \
  || log_warn "Network not found or still in use"

# Clean up gitops-repo git directory
if [[ -d "$PROJECT_ROOT/gitops-repo/.git" ]]; then
  log_info "Cleaning up gitops-repo .git directory..."
  rm -rf "$PROJECT_ROOT/gitops-repo/.git"
  log_ok "Cleaned up"
fi

echo ""
log_ok "Teardown complete. All resources removed."
echo ""
