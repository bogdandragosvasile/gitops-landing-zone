#!/usr/bin/env bash
# Build the landing portal Docker image and import it into k3d.
# k3d image import fails on multi-arch manifests, so we use the
# docker cp + ctr import tarball method (see CLAUDE.md invariants).
source "$(dirname "$0")/lib/common.sh"

require_cmd docker
require_cmd k3d

PORTAL_SRC="$PROJECT_ROOT/gitops-repo/manifests/portal/src"
IMAGE_NAME="landing-portal:latest"
TARBALL="/tmp/landing-portal.tar"

log_info "Building portal image ($IMAGE_NAME)..."
docker build --platform linux/amd64 -t "$IMAGE_NAME" "$PORTAL_SRC" \
  && log_ok "Image built" \
  || { log_error "Docker build failed"; exit 1; }

log_info "Exporting image to tarball..."
docker save "$IMAGE_NAME" -o "$TARBALL"
log_ok "Tarball: $(du -sh "$TARBALL" | cut -f1)"

log_info "Importing into all k3d nodes..."
for node in $(k3d node list --cluster "${K3D_CLUSTER_NAME}" -o json 2>/dev/null \
    | python3 -c "import sys,json; [print(n['name']) for n in json.load(sys.stdin) if 'tools' not in n['name']]" 2>/dev/null); do
  log_info "  → $node"
  docker cp "$TARBALL" "${node}:/tmp/landing-portal.tar" \
    && docker exec "$node" ctr images import /tmp/landing-portal.tar 2>&1 | grep -v "^$" \
    && docker exec "$node" rm /tmp/landing-portal.tar \
    || log_warn "  Failed to import into $node (non-fatal if image already present)"
done

rm -f "$TARBALL"
log_ok "Portal image imported into cluster"
