#!/usr/bin/env bash
# Configure Gitea → ArgoCD webhooks for instant GitOps sync
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

WEBHOOK_URL="http://172.20.0.100/api/webhook"
GITEA_AUTH="${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}"
GITEA_API="${GITEA_EXTERNAL_URL}/api/v1"

REPOS=(
  "platform/gitops-infra"
)

log_info "Configuring Gitea → ArgoCD webhooks..."

for repo in "${REPOS[@]}"; do
  # Check if webhook already exists
  existing=$(curl -sf -u "$GITEA_AUTH" "$GITEA_API/repos/$repo/hooks" 2>/dev/null | grep -c "$WEBHOOK_URL" || true)
  if [ "$existing" -gt 0 ]; then
    log_ok "  $repo — webhook already exists"
    continue
  fi

  # Create webhook
  result=$(curl -sf -u "$GITEA_AUTH" -X POST "$GITEA_API/repos/$repo/hooks" \
    -H "Content-Type: application/json" \
    -d "{
      \"type\": \"gitea\",
      \"config\": {
        \"url\": \"$WEBHOOK_URL\",
        \"content_type\": \"json\"
      },
      \"events\": [\"push\"],
      \"active\": true
    }" 2>&1)

  if echo "$result" | grep -q '"id"'; then
    log_ok "  $repo — webhook created"
  else
    log_warn "  $repo — failed to create webhook: $result"
  fi
done

log_ok "Webhook configuration complete"
log_info "ArgoCD will now sync within seconds of a git push"
