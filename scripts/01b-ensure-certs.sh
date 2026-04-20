#!/usr/bin/env bash
# Generate the self-signed TLS cert for Vaultwarden if missing.
# Runs before 02-start-gitea.sh so `docker compose up -d vaultwarden`
# has something to bind-mount at /ssl.
source "$(dirname "$0")/lib/common.sh"

require_cmd openssl

CERT_DIR="$PROJECT_ROOT/docker-compose/certs"
CERT="$CERT_DIR/vault.crt"
KEY="$CERT_DIR/vault.key"

mkdir -p "$CERT_DIR"

if [[ -s "$CERT" && -s "$KEY" ]]; then
  log_ok "Vaultwarden TLS cert already present ($CERT)"
  exit 0
fi

log_info "Generating self-signed TLS cert for Vaultwarden..."

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout "$KEY" \
  -out    "$CERT" \
  -subj "/C=US/ST=Dev/L=Local/O=GitOpsLandingZone/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:vault.local,IP:127.0.0.1" 2>&1 \
  | tail -1

chmod 600 "$KEY"
chmod 644 "$CERT"

log_ok "Self-signed cert generated: $CERT (valid 10 years)"
