#!/usr/bin/env bash
# Add local DNS entries to the hosts file
source "$(dirname "$0")/lib/common.sh"

MARKER="# gitops-local-dev"

ENTRIES=(
  "127.0.0.1 gitea.local"
  "127.0.0.1 argocd.local"
  "127.0.0.1 keycloak.local"
  "127.0.0.1 portal.local"
  "127.0.0.1 grafana.local"
  "127.0.0.1 prometheus.local"
  "127.0.0.1 vault.local"
  "127.0.0.1 industry40.local"
)

log_info "Configuring local DNS via hosts file (platform: $PLATFORM)..."

if is_unix; then
  # Linux/WSL/macOS all use /etc/hosts.
  HOSTS_FILE="/etc/hosts"

  if grep -q "$MARKER" "$HOSTS_FILE" 2>/dev/null; then
    log_ok "Hosts entries already configured in $HOSTS_FILE"
    exit 0
  fi

  # Build the payload once.
  PAYLOAD=$(
    echo ""
    echo "$MARKER"
    for entry in "${ENTRIES[@]}"; do
      echo "$entry $MARKER"
    done
  )

  # Write strategy:
  #   1. If sudo -n works (cached credential or NOPASSWD), use it — zero prompts.
  #   2. Else if we have a TTY, prompt via sudo -S (interactive).
  #   3. Else on macOS, fall back to osascript with admin privileges (GUI prompt).
  #   4. Else print manual instructions and fail.
  write_hosts() {
    local mode="$1"
    case "$mode" in
      sudo-noprompt)
        printf '%s\n' "$PAYLOAD" | sudo -n tee -a "$HOSTS_FILE" >/dev/null 2>&1
        ;;
      sudo-interactive)
        printf '%s\n' "$PAYLOAD" | sudo tee -a "$HOSTS_FILE" >/dev/null
        ;;
      osascript)
        local esc
        esc=$(printf '%s' "$PAYLOAD" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
        osascript -e "do shell script \"printf %s ${esc} >> $HOSTS_FILE; dscacheutil -flushcache; killall -HUP mDNSResponder\" with administrator privileges" >/dev/null 2>&1
        ;;
    esac
  }

  log_info "Adding entries to $HOSTS_FILE..."
  if write_hosts sudo-noprompt; then
    log_ok "Hosts file updated (cached sudo)"
  elif [[ -t 0 ]]; then
    log_info "  (requires sudo — enter your password)"
    write_hosts sudo-interactive && log_ok "Hosts file updated" \
      || { log_error "sudo write failed"; exit 1; }
  elif [[ "$PLATFORM" == "macos" ]]; then
    log_info "  No TTY — opening macOS admin GUI prompt..."
    write_hosts osascript && log_ok "Hosts file updated (via osascript)" \
      || { log_error "osascript write failed"; exit 1; }
  else
    log_error "No TTY and no GUI — cannot acquire sudo. Run these as root:"
    printf '%s\n' "$PAYLOAD" | while read -r l; do echo "    $l"; done
    exit 1
  fi

  # On macOS, flush the DNS resolver cache so new entries are picked up
  # immediately without waiting for mDNSResponder TTL expiry. osascript
  # already did it inline; plain sudo branch does it here.
  if [[ "$PLATFORM" == "macos" ]]; then
    sudo -n dscacheutil -flushcache 2>/dev/null || true
    sudo -n killall -HUP mDNSResponder 2>/dev/null || true
  fi

else
  # Windows: detect the correct hosts file path (Git Bash vs WSL vs MSYS2)
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

  if grep -q "$MARKER" "$HOSTS_FILE" 2>/dev/null; then
    log_ok "Hosts entries already configured"
    grep "$MARKER" "$HOSTS_FILE"
    exit 0
  fi

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
fi
