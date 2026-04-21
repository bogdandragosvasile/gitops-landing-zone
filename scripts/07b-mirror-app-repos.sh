#!/usr/bin/env bash
# Mirror every subdirectory of `app-repos/` into a Gitea repo under the
# platform org. Creates the Gitea repo if it's missing, then force-pushes
# the local content on `main`.
#
# This is what lets ArgoCD `Application` manifests in gitops-repo/apps/
# point at `http://gitea:3000/${GITEA_ORG}/<name>.git` on a fresh clone —
# without it, the ArgoCD repo-server would get a 404 and the child apps
# would never sync.
#
# Add a new demo/sample app by dropping a directory into `app-repos/`.
# Layout expected:
#
#   app-repos/<name>/
#   ├── manifests/          # k8s YAMLs — matches source.path in the Application
#   ├── Dockerfile          # optional; used by 09c-build-demo-images.sh
#   ├── index.html / ...    # optional image build context
#   ├── build.sh            # optional — 09c runs it if present
#   └── README.md
source "$(dirname "$0")/lib/common.sh"

require_cmd git
require_cmd curl

APP_REPOS_DIR="$PROJECT_ROOT/app-repos"
if [[ ! -d "$APP_REPOS_DIR" ]]; then
  log_warn "No app-repos/ directory found — nothing to mirror."
  exit 0
fi

GITEA_API="http://localhost:${GITEA_HTTP_PORT}/api/v1"
AUTH="${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}"

push_one() {
  local name="$1"
  local src="$2"

  # Create the Gitea repo if missing (idempotent).
  local http
  http=$(curl -sf -o /dev/null -w '%{http_code}' \
    "$GITEA_API/repos/$GITEA_ORG/$name" -u "$AUTH" 2>/dev/null || echo '000')
  if [[ "$http" != "200" ]]; then
    curl -sf -o /dev/null -X POST "$GITEA_API/orgs/$GITEA_ORG/repos" -u "$AUTH" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"$name\",\"auto_init\":false,\"default_branch\":\"main\",\"description\":\"Mirrored from app-repos/$name in gitops-landing-zone\"}" \
      && log_info "  created Gitea repo $GITEA_ORG/$name" \
      || log_warn "  could not create Gitea repo (it may already exist, continuing)"
  fi

  # Stage a throw-away git tree from the source (so we never touch the
  # landing-zone repo's working tree).
  local tmp
  tmp="$(mktemp -d -t "gitops-mirror-$name.XXXXXX")"
  ( cd "$src" && tar cf - . ) | ( cd "$tmp" && tar xf - )

  (
    cd "$tmp"
    git init -q -b main
    git config user.email "bootstrap@local.dev"
    git config user.name  "GitOps Bootstrap"
    git add -A
    git -c commit.gpgsign=false commit -q -m "Mirror from app-repos/$name — $(date -u +%FT%TZ)" \
      >/dev/null 2>&1 || true
    git remote add origin "http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:${GITEA_HTTP_PORT}/${GITEA_ORG}/${name}.git"
    git push -u origin main --force 2>&1 | tail -2
  )

  rm -rf "$tmp"
}

COUNT=0
for dir in "$APP_REPOS_DIR"/*/; do
  [[ -d "$dir" ]] || continue
  name="$(basename "$dir")"
  log_info "Mirroring $name → ${GITEA_ORG}/${name}..."
  push_one "$name" "$dir"
  COUNT=$((COUNT + 1))
done

log_ok "Mirrored $COUNT app repo(s) to Gitea"
