#!/usr/bin/env bash
# Phase 11: generate a Vaultwarden/Bitwarden-compatible import file with
# every platform credential, and install the Bitwarden CLI (bw) so the
# user has both web UI and CLI options to load it.
#
# Output:  ./vaultwarden-import.json  (gitignored)
# Tool:    bw  (macOS: brew install bitwarden-cli; Linux: npm tarball)
#
# Idempotent — re-running regenerates the JSON from the current .env and
# skips bw install if already present.
source "$(dirname "$0")/lib/common.sh"

require_cmd openssl
require_cmd python3

IMPORT_FILE="$PROJECT_ROOT/vaultwarden-import.json"
VAULT_URL="https://localhost:8443"

log_info "Preparing Vaultwarden import file..."

# ── 1. Wait until Vaultwarden answers on :8443 ────────────────────────────────
ELAPSED=0
until curl -sk -o /dev/null -w '%{http_code}' --max-time 3 "$VAULT_URL" 2>/dev/null | grep -qE '^(200|302)$'; do
  if [[ $ELAPSED -ge 60 ]]; then
    log_warn "Vaultwarden not reachable at $VAULT_URL after ${ELAPSED}s"
    log_warn "  Check: docker ps --filter name=vaultwarden"
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done
[[ $ELAPSED -lt 60 ]] && log_ok "Vaultwarden reachable at $VAULT_URL"

# ── 2. Install the Bitwarden CLI (bw) ─────────────────────────────────────────
# Prefer the pre-built zip distribution from vault.bitwarden.com — it's a
# single native binary (~50 MB), installs in a few seconds, and avoids
# brew's from-source compilation path for bitwarden-cli (which drags in
# the full Node toolchain and can take 10+ min on Apple Silicon).
install_bw() {
  mkdir -p "$HOME/bin"
  export PATH="$HOME/bin:$PATH"

  if command -v bw &>/dev/null; then
    log_ok "bw CLI already installed ($(bw --version 2>/dev/null || echo 'unknown version'))"
    return 0
  fi

  local os_slug
  case "$PLATFORM" in
    macos)     os_slug="macos"   ;;
    linux|wsl) os_slug="linux"   ;;
    windows)   os_slug="windows" ;;
    *)
      log_warn "Auto-install of bw not supported on $PLATFORM — install manually from https://bitwarden.com/help/cli/"
      return 1
      ;;
  esac

  local url="https://vault.bitwarden.com/download/?app=cli&platform=$os_slug"
  local tmp_zip="/tmp/bw-$os_slug.zip"

  log_info "Downloading Bitwarden CLI ($os_slug)..."
  if ! curl -sSL -o "$tmp_zip" "$url" 2>/dev/null || [[ ! -s "$tmp_zip" ]]; then
    log_warn "Download failed from $url — falling back to brew if available"
    if [[ "$PLATFORM" == "macos" ]] && command -v brew &>/dev/null; then
      brew install bitwarden-cli >/dev/null 2>&1 \
        && log_ok "bw installed via brew" \
        && return 0
    fi
    log_warn "bw install failed — install manually from https://bitwarden.com/help/cli/"
    return 1
  fi

  if ! command -v unzip &>/dev/null; then
    log_warn "unzip not found — cannot extract bw archive. Install unzip and retry."
    rm -f "$tmp_zip"
    return 1
  fi

  unzip -oqq "$tmp_zip" -d "$HOME/bin" 2>/dev/null
  chmod +x "$HOME/bin/bw" 2>/dev/null || true
  rm -f "$tmp_zip"

  if command -v bw &>/dev/null; then
    log_ok "bw installed to $HOME/bin/bw ($(bw --version 2>/dev/null))"
    return 0
  fi
  log_warn "bw install failed — install manually from https://bitwarden.com/help/cli/"
  return 1
}
install_bw || true

# ── 3. Render the JSON from the current .env ──────────────────────────────────
log_info "Rendering $IMPORT_FILE from .env..."

python3 - "$IMPORT_FILE" "$PROJECT_ROOT/.env" <<'PY'
import json, os, re, sys, uuid

out_path, env_path = sys.argv[1], sys.argv[2]

env = {}
with open(env_path) as f:
    for line in f:
        m = re.match(r'^([A-Z_]+)=(.*)$', line.rstrip('\n'))
        if m:
            env[m.group(1)] = m.group(2)

folder_id = str(uuid.uuid4())

def login(name, uri, username, password, notes=""):
    return {
        "id": str(uuid.uuid4()),
        "organizationId": None,
        "folderId": folder_id,
        "type": 1,
        "reprompt": 0,
        "name": name,
        "notes": notes or None,
        "favorite": False,
        "login": {
            "uris": [{"match": None, "uri": uri}],
            "username": username,
            "password": password,
            "totp": None,
        },
        "collectionIds": None,
    }

def note(name, content):
    return {
        "id": str(uuid.uuid4()),
        "organizationId": None,
        "folderId": folder_id,
        "type": 2,
        "reprompt": 0,
        "name": name,
        "notes": content,
        "favorite": False,
        "secureNote": {"type": 0},
        "collectionIds": None,
    }

items = [
    login(
        "ArgoCD",
        "http://argocd.local",
        "admin",
        env.get("ARGOCD_ADMIN_PASSWORD", ""),
        "GitOps controller. Also accepts Keycloak SSO (LOG IN VIA KEYCLOAK).",
    ),
    login(
        "Gitea",
        f"http://gitea.local:{env.get('GITEA_HTTP_PORT', '3000')}",
        env.get("GITEA_ADMIN_USER", ""),
        env.get("GITEA_ADMIN_PASSWORD", ""),
        "Self-hosted Git + container registry + Actions runner. Also accepts Keycloak SSO.",
    ),
    login(
        "Keycloak (admin)",
        "http://keycloak.local",
        env.get("KEYCLOAK_ADMIN_USER", "admin"),
        env.get("KEYCLOAK_ADMIN_PASSWORD", ""),
        "Identity provider. Admin console — manages the 'gitops' realm used for SSO.",
    ),
    login(
        "Keycloak — dev SSO user",
        "http://keycloak.local/realms/gitops/account",
        "dev",
        "dev",
        "Test user in the 'gitops' realm. Use this to demo SSO on Gitea / ArgoCD.",
    ),
    login(
        "Grafana",
        "http://grafana.local",
        "admin",
        env.get("GRAFANA_ADMIN_PASSWORD", ""),
        "Dashboards — Prometheus + Loki data sources.",
    ),
    login(
        "Prometheus",
        "http://prometheus.local",
        "",
        "",
        "No authentication. Metrics + alerts UI.",
    ),
    login(
        "Landing Portal",
        "http://portal.local",
        "",
        "",
        "Overview page linking all platform services.",
    ),
    login(
        "Vaultwarden Admin Panel",
        "https://localhost:8443/admin",
        "",
        env.get("VAULTWARDEN_ADMIN_TOKEN", ""),
        "Paste the admin token into the single password field on the /admin page.",
    ),
    note(
        "Gitea PostgreSQL — internal",
        "Service: gitea-db (docker compose)\n"
        f"DB:       {env.get('GITEA_DB_NAME', 'gitea')}\n"
        f"User:     {env.get('GITEA_DB_USER', '')}\n"
        f"Password: {env.get('GITEA_DB_PASSWORD', '')}\n\n"
        "Only reachable from inside the 'gitops' docker network.",
    ),
    note(
        "Keycloak PostgreSQL — internal",
        "Service: keycloak-postgres (k3d, namespace keycloak)\n"
        f"DB:       {env.get('KEYCLOAK_DB_NAME', 'keycloak')}\n"
        f"User:     {env.get('KEYCLOAK_DB_USER', '')}\n"
        f"Password: {env.get('KEYCLOAK_DB_PASSWORD', '')}\n\n"
        "Only reachable from inside the cluster (ClusterIP on port 5432).",
    ),
    note(
        "OIDC clients (Keycloak 'gitops' realm)",
        "These are managed by the PostSync configure-job; do not edit in Keycloak directly.\n\n"
        f"Gitea OIDC client\n"
        f"  client_id:     {env.get('OIDC_GITEA_CLIENT_ID', '')}\n"
        f"  client_secret: {env.get('OIDC_GITEA_CLIENT_SECRET', '')}\n"
        f"  redirect_uri:  http://gitea:3000/*\n\n"
        f"ArgoCD OIDC client\n"
        f"  client_id:     {env.get('OIDC_ARGOCD_CLIENT_ID', '')}\n"
        f"  client_secret: {env.get('OIDC_ARGOCD_CLIENT_SECRET', '')}\n"
        f"  redirect_uri:  http://argocd.local/*",
    ),
]

export = {
    "encrypted": False,
    "folders": [{"id": folder_id, "name": "GitOps Landing Zone"}],
    "items": items,
}
with open(out_path, "w") as f:
    json.dump(export, f, indent=2)
PY

chmod 600 "$IMPORT_FILE"
ITEMS=$(python3 -c "import json; print(len(json.load(open('$IMPORT_FILE'))['items']))")
log_ok "Wrote $IMPORT_FILE ($ITEMS items)"

# ── 4. Print instructions ─────────────────────────────────────────────────────
cat <<EOF

==============================================
  Vaultwarden — import your credentials
==============================================

Everything in .env is now in ${IMPORT_FILE#$PROJECT_ROOT/}
(this file is gitignored; delete it after importing).

Option A — Web UI (recommended for first-time setup)

  1. Open  ${VAULT_URL}  in your browser.
     Accept the self-signed cert warning.
  2. Click 'Create account', set a strong master password.
  3. Log in, then go to:  Tools  →  Import data
  4. File format:  Bitwarden (json)
  5. Select file:  ${IMPORT_FILE}
  6. Click 'Import data'.

  All entries land in a new folder called 'GitOps Landing Zone'.

Option B — bw CLI (for scripted / headless imports)
EOF

if command -v bw &>/dev/null; then
  cat <<EOF

  # Vaultwarden uses a self-signed cert — tell bw to skip verification.
  export NODE_TLS_REJECT_UNAUTHORIZED=0
  bw config server ${VAULT_URL}

  # Create an account first via the web UI (Option A step 2), then:
  bw login <email-you-registered>
  export BW_SESSION=\$(bw unlock --raw)
  bw import bitwardenjson ${IMPORT_FILE}

EOF
else
  cat <<EOF

  bw CLI was not installed automatically on this host.
  Install it from https://bitwarden.com/help/cli/ and rerun.

EOF
fi

cat <<EOF
  Cleanup (after a successful import):
    rm ${IMPORT_FILE}

==============================================
EOF
