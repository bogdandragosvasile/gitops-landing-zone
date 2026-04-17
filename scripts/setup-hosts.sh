#!/usr/bin/env bash
# Add local DNS entries to Windows hosts file
# Must be run as Administrator (or from an elevated terminal)
source "$(dirname "$0")/lib/common.sh"

# Detect the correct hosts file path (Git Bash vs WSL vs MSYS2)
if [[ -f "/c/Windows/System32/drivers/etc/hosts" ]]; then
  HOSTS_FILE="/c/Windows/System32/drivers/etc/hosts"
elif [[ -f "/mnt/c/Windows/System32/drivers/etc/hosts" ]]; then
  HOSTS_FILE="/mnt/c/Windows/System32/drivers/etc/hosts"
elif [[ -f "$WINDIR/System32/drivers/etc/hosts" ]]; then
  HOSTS_FILE="$WINDIR/System32/drivers/etc/hosts"
elif [[ -f "$SYSTEMROOT/System32/drivers/etc/hosts" ]]; then
  HOSTS_FILE="$SYSTEMROOT/System32/drivers/etc/hosts"
else
  HOSTS_FILE="/etc/hosts"
fi

MARKER="# gitops-local-dev"

ENTRIES=(
  "127.0.0.1 gitea.local"
  "127.0.0.1 argocd.local"
  "127.0.0.1 keycloak.local"
)

log_info "Configuring local DNS via hosts file..."

# Check if entries already exist
if grep -q "$MARKER" "$HOSTS_FILE" 2>/dev/null; then
  log_ok "Hosts entries already configured"
  grep "$MARKER" "$HOSTS_FILE"
  exit 0
fi

# Append entries
log_info "Adding entries to $HOSTS_FILE..."
{
  echo ""
  echo "$MARKER"
  for entry in "${ENTRIES[@]}"; do
    echo "$entry $MARKER"
  done
} >> "$HOSTS_FILE" 2>/dev/null

if [[ $? -eq 0 ]]; then
  log_ok "Hosts file updated successfully:"
  for entry in "${ENTRIES[@]}"; do
    log_info "  $entry"
  done
else
  log_error "Failed to update hosts file. Run this from an Administrator terminal:"
  echo ""
  echo "  Add these lines to C:\\Windows\\System32\\drivers\\etc\\hosts :"
  echo ""
  for entry in "${ENTRIES[@]}"; do
    echo "    $entry"
  done
  echo ""
  exit 1
fi
