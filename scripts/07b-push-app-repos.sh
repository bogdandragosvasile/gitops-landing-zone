#!/usr/bin/env bash
# Push application repositories to Gitea.
# Creates repos if missing, then force-pushes local app-repos/ content.
#
# Repos pushed:
#   platform/bankoffer-platform  — BankOffer AI Helm chart + source
#   platform/careerforge         — CareerForge kustomize manifests + source
source "$(dirname "$0")/lib/common.sh"

GITEA_URL="http://localhost:${GITEA_HTTP_PORT}"
AUTH="${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}"

push_repo() {
  local repo_name="$1"
  local local_dir="$2"

  log_info "Setting up $repo_name in Gitea..."

  # Create repo if it doesn't exist
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" \
    "$GITEA_URL/api/v1/repos/${GITEA_ORG}/${repo_name}" \
    -u "$AUTH" 2>/dev/null || echo "000")

  if [[ "$HTTP" != "200" ]]; then
    curl -sf -X POST "$GITEA_URL/api/v1/orgs/${GITEA_ORG}/repos" \
      -u "$AUTH" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"${repo_name}\",\"auto_init\":false,\"default_branch\":\"main\"}" \
      >/dev/null 2>&1 \
      && log_ok "  Created $repo_name" \
      || log_warn "  Repo may already exist (OK)"
  else
    log_ok "  $repo_name already exists in Gitea"
  fi

  # Push content
  log_info "  Pushing content to $repo_name..."
  REMOTE="http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:${GITEA_HTTP_PORT}/${GITEA_ORG}/${repo_name}.git"

  (
    cd "$local_dir"
    # Init git if not already
    if [[ ! -d .git ]]; then
      git init -b main
      git config user.email "bootstrap@local.dev"
      git config user.name "Bootstrap"
    fi

    git add -A
    # Commit only if there are changes
    git diff --cached --quiet 2>/dev/null || \
      git commit -m "Bootstrap: initial push of ${repo_name}" --allow-empty-message 2>/dev/null || true

    git remote remove origin 2>/dev/null || true
    git remote add origin "$REMOTE"
    git push -u origin main --force
  ) && log_ok "  $repo_name pushed successfully" \
    || log_warn "  $repo_name push failed — check Gitea connectivity"
}

APP_REPOS_DIR="$PROJECT_ROOT/app-repos"

if [[ ! -d "$APP_REPOS_DIR" ]]; then
  log_warn "app-repos/ directory not found — skipping app repo push"
  exit 0
fi

# Push each app repo
for repo_dir in "$APP_REPOS_DIR"/*/; do
  repo_name="$(basename "$repo_dir")"
  if [[ -d "$repo_dir" ]]; then
    push_repo "$repo_name" "$repo_dir"
  fi
done

log_ok "App repos pushed to Gitea"
