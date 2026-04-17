#!/usr/bin/env bash
# Configure the host DNS to use the local dnsmasq resolver for *.local domains
# On Windows, this sets 127.0.0.1 as a DNS suffix search entry via NRPT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

log_info "Configuring local DNS resolver..."

# Check if dnsmasq container is running
if ! docker ps --filter name=dnsmasq --format '{{.Names}}' | grep -q dnsmasq; then
  log_error "dnsmasq container is not running. Start it with: docker compose up -d dnsmasq"
  exit 1
fi

# Test that dnsmasq resolves *.local
log_info "Testing dnsmasq resolution..."
if nslookup test.local 127.0.0.1 >/dev/null 2>&1; then
  log_ok "dnsmasq is resolving *.local"
else
  log_warn "dnsmasq not responding on 127.0.0.1:53 — may need a moment to start"
fi

# On Windows, configure NRPT (Name Resolution Policy Table) rule for .local
# This tells Windows to use 127.0.0.1 for all *.local queries
log_info "Configuring Windows DNS (NRPT rule for .local)..."
powershell.exe -Command "
  Start-Process powershell -ArgumentList @(
    '-Command',
    'Add-DnsClientNrptRule -Namespace \".local\" -NameServers \"127.0.0.1\" -ErrorAction SilentlyContinue;
     Get-DnsClientNrptRule | Where-Object { \$_.Namespace -eq \".local\" } | Format-Table -AutoSize'
  ) -Verb RunAs -Wait
" 2>/dev/null || {
  log_warn "Could not set NRPT rule (UAC denied or not available)"
  log_info "Falling back to hosts file approach..."

  # Fallback: ensure hosts file has entries
  HOSTS_FILE="/c/Windows/System32/drivers/etc/hosts"
  MARKER="# gitops-local-dev"
  ENTRIES=(
    "127.0.0.1 gitea.local"
    "127.0.0.1 argocd.local"
    "127.0.0.1 keycloak.local"
    "127.0.0.1 portal.local"
    "127.0.0.1 grafana.local"
    "127.0.0.1 prometheus.local"
    "127.0.0.1 vault.local"
  )

  missing=0
  for entry in "${ENTRIES[@]}"; do
    host=$(echo "$entry" | awk '{print $2}')
    if ! grep -q "$host" "$HOSTS_FILE" 2>/dev/null; then
      missing=$((missing + 1))
    fi
  done

  if [ "$missing" -gt 0 ]; then
    log_info "  $missing entries missing from hosts file"
    log_info "  Run from elevated PowerShell:"
    for entry in "${ENTRIES[@]}"; do
      host=$(echo "$entry" | awk '{print $2}')
      if ! grep -q "$host" "$HOSTS_FILE" 2>/dev/null; then
        echo "    Add-Content -Path C:\\Windows\\System32\\drivers\\etc\\hosts -Value '$entry $MARKER'"
      fi
    done
  else
    log_ok "All entries present in hosts file"
  fi
}

log_ok "DNS configuration complete"
log_info ""
log_info "Test with: nslookup portal.local 127.0.0.1"
log_info "Debug with: docker logs dnsmasq"
