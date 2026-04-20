#!/usr/bin/env bash
# Common functions for bootstrap scripts

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure user bin directory is on PATH (where k3d/helm/argocd are installed).
# Also include Homebrew prefixes (Apple Silicon: /opt/homebrew, Intel: /usr/local).
export PATH="$HOME/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Always use the default kubeconfig (~/.kube/config) so that k3d kubeconfig merge
# is the single source of truth. Unsetting removes any stale KUBECONFIG env var
# that might point to a separate k3d-specific file from a previous session.
unset KUBECONFIG

# Load .env
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

# Detect the host platform.
# Returns: wsl | linux | macos | windows
detect_platform() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || echo unknown)"
  if grep -qi microsoft /proc/version 2>/dev/null; then
    echo "wsl"
  elif [[ "$uname_s" == "Linux" ]]; then
    echo "linux"
  elif [[ "$uname_s" == "Darwin" ]]; then
    echo "macos"
  else
    echo "windows"
  fi
}

# Detect CPU architecture, normalized for download URLs.
# Returns: amd64 | arm64
detect_arch() {
  local arch
  arch="$(uname -m 2>/dev/null || echo unknown)"
  case "$arch" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) echo "amd64" ;;  # fallback
  esac
}

PLATFORM="$(detect_platform)"
PLATFORM_ARCH="$(detect_arch)"

# Helper — true on unix-like platforms (macos + linux + wsl).
is_unix() {
  [[ "$PLATFORM" == "macos" || "$PLATFORM" == "linux" || "$PLATFORM" == "wsl" ]]
}

# Build the docker compose -f flags.
# On Linux/WSL/macOS the "unix" override removes the dnsmasq port-53 host binding:
#   - Linux: systemd-resolved conflicts with the bind.
#   - macOS: mDNSResponder does not use 53, but Colima/Docker Desktop cannot
#     forward privileged ports reliably and :53 is often already bound.
COMPOSE_DIR_DEFAULT="$PROJECT_ROOT/docker-compose"
if is_unix; then
  # docker-compose.linux.yml is the legacy name kept for backwards compatibility
  # (it applies equally to macOS — the override only disables dnsmasq host ports).
  COMPOSE_FILES="-f $COMPOSE_DIR_DEFAULT/docker-compose.yml -f $COMPOSE_DIR_DEFAULT/docker-compose.linux.yml"
else
  COMPOSE_FILES="-f $COMPOSE_DIR_DEFAULT/docker-compose.yml"
fi

log_info() {
  echo -e "\033[1;34m[INFO]\033[0m $*"
}

log_ok() {
  echo -e "\033[1;32m[ OK ]\033[0m $*"
}

log_warn() {
  echo -e "\033[1;33m[WARN]\033[0m $*"
}

log_error() {
  echo -e "\033[1;31m[ERR ]\033[0m $*"
}

# Wait for an HTTP endpoint to return 200
# Usage: wait_for_url <url> [timeout_seconds] [description]
wait_for_url() {
  local url="$1"
  local timeout="${2:-120}"
  local desc="${3:-$url}"
  local elapsed=0

  log_info "Waiting for $desc to be ready..."
  while [[ $elapsed -lt $timeout ]]; do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then
      log_ok "$desc is ready (${elapsed}s)"
      return 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  log_error "$desc not ready after ${timeout}s"
  return 1
}

# Wait for a kubernetes deployment to be available
# Usage: wait_for_deployment <namespace> <deployment> [timeout_seconds]
wait_for_deployment() {
  local ns="$1"
  local deploy="$2"
  local timeout="${3:-180}"

  log_info "Waiting for deployment $deploy in namespace $ns..."
  if kubectl wait --for=condition=available deployment/"$deploy" \
    -n "$ns" --timeout="${timeout}s" 2>/dev/null; then
    log_ok "Deployment $deploy is available"
    return 0
  fi
  log_error "Deployment $deploy not available after ${timeout}s"
  return 1
}

# Wait for all pods in a namespace to be ready
# Usage: wait_for_pods <namespace> [timeout_seconds]
wait_for_pods() {
  local ns="$1"
  local timeout="${2:-180}"
  local elapsed=0

  log_info "Waiting for all pods in namespace $ns to be ready..."
  while [[ $elapsed -lt $timeout ]]; do
    local not_ready
    not_ready=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null \
      | grep -v -E '(Running|Completed|Succeeded)' | wc -l || true)
    if [[ "$not_ready" -eq 0 ]]; then
      local total
      total=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | wc -l || echo "0")
      if [[ "$total" -gt 0 ]]; then
        log_ok "All $total pods in $ns are ready (${elapsed}s)"
        return 0
      fi
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  log_error "Pods in $ns not all ready after ${timeout}s"
  kubectl get pods -n "$ns" 2>/dev/null || true
  return 1
}

# Generate a random password
# Usage: generate_password [length]
generate_password() {
  local length="${1:-24}"
  openssl rand -base64 "$length" | tr -dc 'A-Za-z0-9!@#' | head -c "$length"
}

# Check if a command exists
require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" &>/dev/null; then
    log_error "Required command not found: $cmd"
    return 1
  fi
}
