#!/usr/bin/env bash
# For every subdir of `app-repos/` that has a `build.sh`, run it.
# Each build.sh is expected to produce + import a k3d-local image.
#
# This lets demo apps like industry40 (which needs its HTML baked into
# nginx:alpine and imported on every k3d node) deploy fully offline on
# a fresh clone — no registry round-trip needed.
#
# Apps without a build.sh (e.g. headscale — uses a public registry image)
# are silently skipped.
source "$(dirname "$0")/lib/common.sh"

require_cmd docker
require_cmd k3d

APP_REPOS_DIR="$PROJECT_ROOT/app-repos"
if [[ ! -d "$APP_REPOS_DIR" ]]; then
  log_warn "No app-repos/ directory found — nothing to build."
  exit 0
fi

BUILT=0
SKIPPED=0
for dir in "$APP_REPOS_DIR"/*/; do
  [[ -d "$dir" ]] || continue
  name="$(basename "$dir")"
  if [[ ! -x "$dir/build.sh" ]]; then
    log_info "skipping $name (no build.sh)"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  log_info "Running $name/build.sh ..."
  ( cd "$dir" && bash ./build.sh 2>&1 ) | tail -5 \
    && { log_ok "  $name image built + imported"; BUILT=$((BUILT + 1)); } \
    || log_warn "  $name build failed (non-fatal; the app will stay ErrImageNeverPull)"
done

log_ok "Built $BUILT image(s), skipped $SKIPPED"
