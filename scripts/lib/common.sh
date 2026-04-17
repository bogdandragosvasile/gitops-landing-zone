#!/usr/bin/env bash
# Common functions for bootstrap scripts

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure user bin directory is on PATH (where k3d/helm/argocd are installed)
export PATH="$HOME/bin:$PATH"

# Load .env
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
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
