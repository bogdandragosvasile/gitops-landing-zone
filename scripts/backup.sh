#!/usr/bin/env bash
# Backup — creates a timestamped archive of all landing zone data.
# Dumps: Gitea DB, Keycloak realms, Vaultwarden SQLite, .env
# Extend: add your app-specific pg_dump / kubectl exec blocks in the CUSTOM section.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/common.sh"

TIMESTAMP=$(date -u +%Y-%m-%d-%H%M%S)
BACKUP_DIR="$PROJECT_ROOT/backups"
WORK_DIR="$BACKUP_DIR/backup-$TIMESTAMP"
ARCHIVE="$BACKUP_DIR/backup-$TIMESTAMP.tar.gz"
mkdir -p "$WORK_DIR"

[ -f "$PROJECT_ROOT/.env" ] && { set -a; source "$PROJECT_ROOT/.env"; set +a; }

log_info "Backup started: $TIMESTAMP"
errors=0

# Gitea DB
log_info "Dumping Gitea database..."
if docker exec gitea-db pg_dump -U "${GITEA_DB_USER:-gitea}" -d "${GITEA_DB_NAME:-gitea}" \
     --clean --if-exists > "$WORK_DIR/gitea-db.sql" 2>/dev/null; then
  log_ok "  Gitea: $(wc -c < "$WORK_DIR/gitea-db.sql") bytes"
else
  log_warn "  Gitea dump failed"; errors=$((errors+1))
fi

# Keycloak realms
log_info "Exporting Keycloak realms..."
KC_URL="${KEYCLOAK_URL:-http://keycloak.local}"
KC_TOKEN=$(curl -sf -X POST "$KC_URL/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=${KEYCLOAK_ADMIN_USER:-admin}&password=${KEYCLOAK_ADMIN_PASSWORD:-}" \
  2>/dev/null | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -n "$KC_TOKEN" ]; then
  mkdir -p "$WORK_DIR/keycloak"
  for realm in $(curl -sf -H "Authorization: Bearer $KC_TOKEN" "$KC_URL/admin/realms" \
                 | grep -o '"realm":"[^"]*"' | cut -d'"' -f4); do
    curl -sf -H "Authorization: Bearer $KC_TOKEN" "$KC_URL/admin/realms/$realm" \
      > "$WORK_DIR/keycloak/$realm.json" && log_ok "  Realm: $realm"
  done
else
  log_warn "  Keycloak auth failed"; errors=$((errors+1))
fi

# Vaultwarden
log_info "Copying Vaultwarden data..."
if docker cp vaultwarden:/data/db.sqlite3 "$WORK_DIR/vaultwarden-db.sqlite3" 2>/dev/null; then
  log_ok "  Vaultwarden: $(wc -c < "$WORK_DIR/vaultwarden-db.sqlite3") bytes"
else
  log_warn "  Vaultwarden copy failed"; errors=$((errors+1))
fi

# .env
[ -f "$PROJECT_ROOT/.env" ] && cp "$PROJECT_ROOT/.env" "$WORK_DIR/.env" && log_ok "  .env copied"

# ── CUSTOM: add your application backups below ──────────────────────────────
# Example — dump a PostgreSQL pod in a custom namespace:
#
# APP_POD=$(MSYS_NO_PATHCONV=1 kubectl get pods -n my-app -l app=postgres \
#   -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
# if [ -n "$APP_POD" ]; then
#   MSYS_NO_PATHCONV=1 kubectl exec -n my-app "$APP_POD" -- \
#     pg_dump -U myuser -d mydb --clean --if-exists > "$WORK_DIR/my-app-db.sql" 2>/dev/null \
#     && log_ok "  my-app DB dumped"
# fi

# Tarball
log_info "Creating archive..."
tar czf "$ARCHIVE" -C "$BACKUP_DIR" "backup-$TIMESTAMP"
rm -rf "$WORK_DIR"

log_ok "Archive: $ARCHIVE ($(wc -c < "$ARCHIVE") bytes)"
[ "$errors" -gt 0 ] && log_warn "Completed with $errors warnings" || log_ok "Completed successfully"
