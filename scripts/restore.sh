#!/usr/bin/env bash
# Restore — restores all landing zone data from a backup archive.
# Usage: ./scripts/restore.sh backups/backup-YYYY-MM-DD-HHMMSS.tar.gz
# Extend: add your app-specific restore blocks in the CUSTOM section.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/common.sh"

ARCHIVE="${1:-}"
[ -z "$ARCHIVE" ] && { log_error "Usage: $0 <backup-archive.tar.gz>"; exit 1; }
[ ! -f "$ARCHIVE" ] && { log_error "Not found: $ARCHIVE"; exit 1; }

EXTRACT_DIR=$(mktemp -d)
trap "rm -rf $EXTRACT_DIR" EXIT

log_info "Extracting archive..."
tar xzf "$ARCHIVE" -C "$EXTRACT_DIR"
WORK_DIR="$EXTRACT_DIR/$(ls "$EXTRACT_DIR" | head -1)"
log_ok "Extracted to $WORK_DIR"

# Restore .env first (may contain creds needed for subsequent steps)
if [ -f "$WORK_DIR/.env" ]; then
  [ -f "$PROJECT_ROOT/.env" ] && cp "$PROJECT_ROOT/.env" "$PROJECT_ROOT/.env.pre-restore.bak"
  cp "$WORK_DIR/.env" "$PROJECT_ROOT/.env"
  set -a; source "$PROJECT_ROOT/.env"; set +a
  log_ok ".env restored"
fi

errors=0

# Gitea DB
if [ -f "$WORK_DIR/gitea-db.sql" ]; then
  log_info "Restoring Gitea DB..."
  docker exec -i gitea-db psql -U "${GITEA_DB_USER:-gitea}" -d "${GITEA_DB_NAME:-gitea}" \
    < "$WORK_DIR/gitea-db.sql" >/dev/null 2>&1 && log_ok "  Restored" || { log_warn "  Failed"; errors=$((errors+1)); }
fi

# Keycloak realms
if [ -d "$WORK_DIR/keycloak" ]; then
  log_info "Restoring Keycloak realms..."
  KC_URL="${KEYCLOAK_URL:-http://keycloak.local}"
  KC_TOKEN=$(curl -sf -X POST "$KC_URL/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password&client_id=admin-cli&username=${KEYCLOAK_ADMIN_USER:-admin}&password=${KEYCLOAK_ADMIN_PASSWORD:-}" \
    2>/dev/null | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

  if [ -n "$KC_TOKEN" ]; then
    for realm_file in "$WORK_DIR"/keycloak/*.json; do
      realm=$(basename "$realm_file" .json)
      # Try update first; if not found, create
      if curl -sf -X PUT -H "Authorization: Bearer $KC_TOKEN" -H "Content-Type: application/json" \
           -d "@$realm_file" "$KC_URL/admin/realms/$realm" >/dev/null 2>&1; then
        log_ok "  Updated realm: $realm"
      elif curl -sf -X POST -H "Authorization: Bearer $KC_TOKEN" -H "Content-Type: application/json" \
           -d "@$realm_file" "$KC_URL/admin/realms" >/dev/null 2>&1; then
        log_ok "  Created realm: $realm"
      else
        log_warn "  Failed: $realm"; errors=$((errors+1))
      fi
    done
  fi
fi

# Vaultwarden
if [ -f "$WORK_DIR/vaultwarden-db.sqlite3" ]; then
  log_info "Restoring Vaultwarden..."
  docker stop vaultwarden >/dev/null 2>&1
  docker cp "$WORK_DIR/vaultwarden-db.sqlite3" vaultwarden:/data/db.sqlite3
  docker start vaultwarden >/dev/null 2>&1
  log_ok "  Restored"
fi

# ── CUSTOM: add your application restores below ─────────────────────────────
# Example — restore a PostgreSQL pod in a custom namespace:
#
# if [ -f "$WORK_DIR/my-app-db.sql" ]; then
#   APP_POD=$(MSYS_NO_PATHCONV=1 kubectl get pods -n my-app -l app=postgres \
#     -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
#   if [ -n "$APP_POD" ]; then
#     MSYS_NO_PATHCONV=1 kubectl exec -i -n my-app "$APP_POD" -- \
#       psql -U myuser -d mydb < "$WORK_DIR/my-app-db.sql" >/dev/null 2>&1 \
#       && log_ok "  my-app DB restored"
#   fi
# fi

[ "$errors" -gt 0 ] && log_warn "Completed with $errors warnings" || log_ok "Restore completed successfully"
