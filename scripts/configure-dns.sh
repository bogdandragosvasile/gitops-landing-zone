#!/usr/bin/env bash
# Configure host DNS for *.local resolution.
# Windows: sets an NRPT rule pointing *.local to the local dnsmasq container.
# Linux/WSL: validates /etc/hosts entries (added by setup-hosts.sh).
#            dnsmasq port-53 is NOT exposed to the host on Linux (systemd-resolved conflict),
#            so in-cluster DNS uses dnsmasq at 172.20.0.2 and the host uses /etc/hosts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

MARKER="# gitops-local-dev"
HOSTNAMES=(gitea.local argocd.local keycloak.local portal.local grafana.local prometheus.local vault.local)

log_info "Configuring local DNS resolver (platform: $PLATFORM)..."

# Check dnsmasq is running (needed on all platforms for in-cluster DNS)
if ! docker ps --filter name=dnsmasq --format '{{.Names}}' | grep -q dnsmasq; then
  log_error "dnsmasq container is not running. Start it with: docker compose up -d dnsmasq"
  exit 1
fi

if [[ "$PLATFORM" == "wsl" || "$PLATFORM" == "linux" ]]; then
  # Host resolution via /etc/hosts — validate entries are present
  log_info "Checking /etc/hosts entries..."
  missing=0
  for host in "${HOSTNAMES[@]}"; do
    if ! grep -q "$host" /etc/hosts 2>/dev/null; then
      log_warn "  Missing: $host"
      missing=$((missing + 1))
    fi
  done

  if [[ "$missing" -gt 0 ]]; then
    log_info "Running setup-hosts.sh to add missing entries..."
    bash "$SCRIPT_DIR/setup-hosts.sh"
  else
    log_ok "All /etc/hosts entries present"
  fi

  log_info ""
  log_info "dnsmasq serves in-cluster DNS at 172.20.0.2:53 (Docker network only)."
  log_info "Test in-cluster: kubectl run -it --rm dns-test --image=busybox --restart=Never -- nslookup gitea.local 172.20.0.2"

else
  # Windows: configure NRPT rule for .local → dnsmasq at 127.0.0.1:53
  log_info "Testing dnsmasq resolution..."
  if nslookup test.local 127.0.0.1 >/dev/null 2>&1; then
    log_ok "dnsmasq is resolving *.local"
  else
    log_warn "dnsmasq not responding on 127.0.0.1:53 — may need a moment to start"
  fi

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
    bash "$SCRIPT_DIR/setup-hosts.sh"
  }
fi

log_ok "DNS configuration complete"
log_info "Test with: curl -s http://portal.local"
log_info "Debug dnsmasq with: docker logs dnsmasq"
