#!/usr/bin/env bash
# Create the shared Docker network for gitops
source "$(dirname "$0")/lib/common.sh"

log_info "Setting up Docker network: ${DOCKER_NETWORK}"

# Check if network already exists
if docker network inspect "$DOCKER_NETWORK" &>/dev/null; then
  log_ok "Network '$DOCKER_NETWORK' already exists"
else
  log_info "Creating Docker network '$DOCKER_NETWORK'..."
  docker network create "$DOCKER_NETWORK" \
    --driver bridge \
    --subnet 172.20.0.0/24 \
    --gateway 172.20.0.1
  log_ok "Network '$DOCKER_NETWORK' created with subnet 172.18.200.0/24"
fi

# Inspect and display network info
SUBNET=$(docker network inspect "$DOCKER_NETWORK" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')
GATEWAY=$(docker network inspect "$DOCKER_NETWORK" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')

log_info "Network details:"
log_info "  Subnet:  $SUBNET"
log_info "  Gateway: $GATEWAY"
log_info "  MetalLB range: ${METALLB_IP_START} - ${METALLB_IP_END}"

log_ok "Docker network ready"
