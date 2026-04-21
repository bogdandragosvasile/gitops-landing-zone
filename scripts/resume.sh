#!/usr/bin/env bash
# Graceful resume after a `colima stop` / `teardown-stop` cycle.
# Brings Colima + compose + k3d back up and re-applies the transient fixes
# that don't survive a Docker-daemon restart (k3d node DNS, CoreDNS
# NodeHosts for gitea.local). Idempotent — safe to re-run.
#
#   ./scripts/resume.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

log_info "Resuming landing zone on $PLATFORM..."

# ── 1. Colima ─────────────────────────────────────────────────────────────────
if [[ "$PLATFORM" == "macos" ]]; then
  if ! colima status &>/dev/null; then
    log_info "Starting Colima..."
    colima start 2>&1 | tail -3
  else
    log_ok "Colima already running"
  fi

  # The daemon-DNS pin sometimes isn't wired (if the cluster was bootstrapped
  # before v1.5.1). Make sure the VM's Docker daemon has public DNS so newly
  # created containers — including restarted k3d nodes — inherit it.
  log_info "Ensuring Docker daemon DNS is pinned (8.8.8.8 / 1.1.1.1)..."
  NEW_JSON_B64=$(python3 - <<'PY' | base64
import json, os, subprocess, sys
# Read the existing daemon.json from inside the Colima VM via colima ssh.
try:
    cur = subprocess.check_output(
        ["colima", "ssh", "--", "cat", "/etc/docker/daemon.json"],
        stderr=subprocess.DEVNULL
    ).decode() or "{}"
    d = json.loads(cur)
except Exception:
    d = {}
d["dns"] = ["8.8.8.8", "1.1.1.1"]
sys.stdout.write(json.dumps(d, indent=2))
PY
  )
  colima ssh -- sudo sh -c "echo '$NEW_JSON_B64' | base64 -d > /etc/docker/daemon.json && (systemctl reload docker || systemctl restart docker)" \
    2>/dev/null && log_ok "daemon.json updated" \
                || log_warn "Could not update daemon.json (continuing — node-level DNS patch still runs below)"
fi

# ── 2. docker compose stack ──────────────────────────────────────────────────
log_info "Starting compose stack (gitea, db, runner, vaultwarden, dnsmasq)..."
docker compose $COMPOSE_FILES --env-file "$PROJECT_ROOT/.env" start 2>&1 | tail -5
log_ok "Compose stack started"

# ── 3. k3d cluster ───────────────────────────────────────────────────────────
log_info "Starting k3d cluster '${K3D_CLUSTER_NAME}'..."
k3d cluster start "${K3D_CLUSTER_NAME}" 2>&1 | tail -3

# Wait for the API server
ELAPSED=0
while [[ $ELAPSED -lt 60 ]]; do
  if kubectl cluster-info &>/dev/null; then
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done
kubectl wait --for=condition=Ready nodes --all --timeout=120s >/dev/null 2>&1 || true
log_ok "All nodes ready"

# ── 4. Re-patch k3d node DNS (Docker wipes /etc/resolv.conf on container start) ─
if [[ "$PLATFORM" == "macos" ]]; then
  log_info "Re-patching k3d node DNS for Colima..."
  for node in $(k3d node list -o json 2>/dev/null \
      | python3 -c "import sys,json
for n in json.load(sys.stdin):
    if n.get('role') not in ('server','agent'): continue
    if n.get('runtimeLabels',{}).get('k3d.cluster') != '${K3D_CLUSTER_NAME}': continue
    print(n['name'])" 2>/dev/null); do
    docker exec "$node" sh -c 'printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions ndots:0\n" > /etc/resolv.conf' \
      && log_info "  → $node DNS patched"
  done
fi

# ── 5. Re-patch CoreDNS NodeHosts for gitea.local ────────────────────────────
GITEA_IP=$(docker inspect gitea --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
if [[ -n "$GITEA_IP" ]]; then
  log_info "Patching CoreDNS NodeHosts: gitea.local → $GITEA_IP..."
  kubectl -n kube-system get cm coredns -o json \
    | sed "s|\"NodeHosts\": \"|\"NodeHosts\": \"$GITEA_IP gitea.local\\n|" \
    | kubectl apply -f - >/dev/null 2>&1
  kubectl -n kube-system rollout restart deploy coredns >/dev/null 2>&1 || true
  log_ok "CoreDNS patched"
fi

# ── 6. Clear any ghost pods from the pre-stop state ──────────────────────────
log_info "Cleaning up ghost Pending pods from pre-stop state..."
kubectl delete pod -A --field-selector=status.phase=Pending --ignore-not-found >/dev/null 2>&1 || true

log_ok "Resume complete"
log_info "  kubectl get applications -n argocd   # watch ArgoCD sync as things re-converge"
