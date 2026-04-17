#!/usr/bin/env bash
# Build and import all application images into k3d.
#
# Handles:
#   bankoffer-slides:dev      — built from manifests/bankoffer-slides/src/
#   careerforge-slides:dev    — built from manifests/careerforge-slides/src/
#   bankoffer-api:dev         — built from app-repos/bankoffer-platform/
#   bankoffer-postgresql:dev  — pulled from bitnami/postgresql:16
#   careerforge-*:dev         — imported if present locally (pre-built by CI)
#
# Uses docker cp + ctr import (k3d multi-arch tarball workaround).
source "$(dirname "$0")/lib/common.sh"

require_cmd docker
require_cmd k3d

import_image() {
  local image="$1"
  local tarball="/tmp/$(echo "$image" | tr ':/' '--').tar"

  log_info "Exporting $image..."
  docker save "$image" -o "$tarball"

  log_info "Importing $image into k3d nodes..."
  for node in $(k3d node list --cluster "${K3D_CLUSTER_NAME}" -o json 2>/dev/null \
      | python3 -c "import sys,json; [print(n['name']) for n in json.load(sys.stdin) if 'tools' not in n['name']]" 2>/dev/null); do
    log_info "  → $node"
    docker cp "$tarball" "${node}:/tmp/import.tar" \
      && docker exec "$node" ctr images import /tmp/import.tar 2>&1 | grep -v "^$" \
      && docker exec "$node" rm /tmp/import.tar \
      || log_warn "  Failed to import into $node"
  done

  rm -f "$tarball"
  log_ok "$image imported"
}

build_and_import() {
  local image="$1"
  local src_dir="$2"

  log_info "Building $image from $src_dir..."
  docker build --platform linux/amd64 -t "$image" "$src_dir" \
    && log_ok "$image built" \
    || { log_error "Docker build failed for $image"; return 1; }

  import_image "$image"
}

# ── Slide images — built from source in this repo ──────────────────────────
build_and_import "bankoffer-slides:dev"   "$PROJECT_ROOT/gitops-repo/manifests/bankoffer-slides/src"
build_and_import "careerforge-slides:dev" "$PROJECT_ROOT/gitops-repo/manifests/careerforge-slides/src"

# ── BankOffer API — built from Dockerfile in app-repos/bankoffer-platform ──
BANKOFFER_PLATFORM="$PROJECT_ROOT/app-repos/bankoffer-platform"
if [[ -f "$BANKOFFER_PLATFORM/Dockerfile" ]]; then
  log_info "Building bankoffer-api:dev from source..."
  docker build --platform linux/amd64 -t "bankoffer-api:dev" "$BANKOFFER_PLATFORM" \
    && log_ok "bankoffer-api:dev built" \
    || log_warn "bankoffer-api:dev build failed — app may not deploy correctly"
  docker images -q bankoffer-api:dev | grep -q . && import_image "bankoffer-api:dev"
else
  if docker images -q bankoffer-api:dev | grep -q .; then
    log_ok "bankoffer-api:dev already present — importing"
    import_image "bankoffer-api:dev"
  else
    log_warn "bankoffer-api:dev not found and no Dockerfile — BankOffer app will stay Pending"
  fi
fi

# ── BankOffer PostgreSQL — pulled from Bitnami ─────────────────────────────
if docker images -q bankoffer-postgresql:dev | grep -q .; then
  log_ok "bankoffer-postgresql:dev already present — importing"
else
  log_info "Pulling bitnami/postgresql:16 → bankoffer-postgresql:dev..."
  docker pull bitnami/postgresql:16 \
    && docker tag bitnami/postgresql:16 bankoffer-postgresql:dev \
    && log_ok "bankoffer-postgresql:dev ready" \
    || log_warn "Pull failed — check network and retry"
fi
docker images -q bankoffer-postgresql:dev | grep -q . && import_image "bankoffer-postgresql:dev"

# ── CareerForge images — import if present (built by Gitea CI or pre-pulled) ─
CF_IMAGES=(
  "careerforge-backend:dev"
  "careerforge-admin-portal:dev"
  "careerforge-coach-portal:dev"
  "careerforge-employee-portal:dev"
)
CF_MISSING=()
for img in "${CF_IMAGES[@]}"; do
  if docker images -q "$img" | grep -q .; then
    import_image "$img"
  else
    CF_MISSING+=("$img")
  fi
done

if [[ ${#CF_MISSING[@]} -gt 0 ]]; then
  log_warn "CareerForge images not found locally:"
  for img in "${CF_MISSING[@]}"; do
    log_warn "  $img"
  done
  log_warn "CareerForge pods will stay Pending until these images are available."
  log_warn "Build them via Gitea CI after running the careerforge runner, or pull manually."
fi

log_ok "Application image import complete"
