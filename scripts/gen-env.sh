#!/usr/bin/env bash
# Generate a fresh .env from .env.example with URL-safe random secrets.
# Idempotent: does nothing if .env already exists (unless --force).
#
#   ./scripts/gen-env.sh          # only if .env is missing
#   ./scripts/gen-env.sh --force  # overwrite .env (backs up current to .env.bak)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi

ENV_FILE="$PROJECT_ROOT/.env"
ENV_TEMPLATE="$PROJECT_ROOT/.env.example"

if [[ -f "$ENV_FILE" && "$FORCE" -ne 1 ]]; then
  log_ok ".env already exists — skipping generation (use --force to overwrite)"
  exit 0
fi

if [[ ! -f "$ENV_TEMPLATE" ]]; then
  log_error "Template not found: $ENV_TEMPLATE"
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
  log_info "Backed up existing .env"
fi

log_info "Generating fresh URL-safe secrets..."

# All passwords restricted to alnum — '+', '/', '=' break form-urlencoded
# admin-cli token requests to Keycloak (see common.sh:generate_password).
GITEA_PW=$(generate_password 32)
GITEA_DB_PW=$(generate_password 32)
ARGO_PW=$(generate_password 32)
KC_PW=$(generate_password 32)
KC_DB_PW=$(generate_password 32)
VAULT_TOK=$(generate_password 64)
GRAFANA_PW=$(generate_password 32)
GITEA_OIDC=$(generate_hex 24)
ARGO_OIDC=$(generate_hex 24)

# Use a python inline substitution to avoid sed delimiter issues.
# Only replaces lines of the form NAME=CHANGE_ME_...
export G_GITEA_PW="$GITEA_PW" G_GITEA_DB_PW="$GITEA_DB_PW" G_ARGO_PW="$ARGO_PW" \
       G_KC_PW="$KC_PW" G_KC_DB_PW="$KC_DB_PW" G_VAULT_TOK="$VAULT_TOK" \
       G_GRAFANA_PW="$GRAFANA_PW" G_GITEA_OIDC="$GITEA_OIDC" G_ARGO_OIDC="$ARGO_OIDC"

python3 - "$ENV_TEMPLATE" "$ENV_FILE" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f: s = f.read()
subs = {
  "GITEA_ADMIN_PASSWORD":      os.environ["G_GITEA_PW"],
  "GITEA_DB_PASSWORD":         os.environ["G_GITEA_DB_PW"],
  "ARGOCD_ADMIN_PASSWORD":     os.environ["G_ARGO_PW"],
  "KEYCLOAK_ADMIN_PASSWORD":   os.environ["G_KC_PW"],
  "KEYCLOAK_DB_PASSWORD":      os.environ["G_KC_DB_PW"],
  "OIDC_GITEA_CLIENT_SECRET":  os.environ["G_GITEA_OIDC"],
  "OIDC_ARGOCD_CLIENT_SECRET": os.environ["G_ARGO_OIDC"],
  "VAULTWARDEN_ADMIN_TOKEN":   os.environ["G_VAULT_TOK"],
  "GRAFANA_ADMIN_PASSWORD":    os.environ["G_GRAFANA_PW"],
}
for k, v in subs.items():
    s = re.sub(rf'^{k}=.*$', f'{k}={v}', s, flags=re.M)
with open(dst, 'w') as f: f.write(s)
PY

chmod 600 "$ENV_FILE"
log_ok "Generated $ENV_FILE with fresh URL-safe secrets"
log_info "All secrets are alnum-only (no +/=) — safe for form-urlencoded requests"
