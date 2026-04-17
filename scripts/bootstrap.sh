#!/usr/bin/env bash
# Master bootstrap script - sets up the entire local GitOps environment
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "=============================================="
echo "  Local GitOps Landing Zone - Bootstrap"
echo "=============================================="
echo ""

run_step() {
  local step="$1"
  local desc="$2"
  echo ""
  echo "----------------------------------------------"
  echo "  Step: $desc"
  echo "----------------------------------------------"
  bash "$SCRIPT_DIR/$step"
}

run_step "setup-hosts.sh"            "Configure local DNS (hosts file)"
run_step "00-prerequisites.sh"       "Install prerequisites (k3d, helm, argocd CLI)"
run_step "01-create-network.sh"      "Create shared Docker network"
run_step "02-start-gitea.sh"         "Start Gitea stack"
run_step "03-configure-gitea.sh"     "Configure Gitea (admin, org, runner)"
run_step "04-create-k3d-cluster.sh"  "Create k3d Kubernetes cluster"
run_step "05-install-metallb.sh"     "Install MetalLB load balancer"
run_step "06-install-argocd.sh"      "Bootstrap ArgoCD"
run_step "07-push-gitops-repo.sh"    "Push gitops-infra repo to Gitea"
run_step "08-configure-argocd-repo.sh" "Register Gitea repo in ArgoCD"
run_step "09-apply-app-of-apps.sh"   "Activate app-of-apps"
run_step "10-configure-oidc.sh"      "Configure Keycloak OIDC SSO"

echo ""
echo "=============================================="
echo "  Bootstrap Complete!"
echo "=============================================="
echo ""
echo "  Services:"
echo "    Gitea:    http://gitea.local:3000"
echo "      Admin:  gitea_admin (see .env for password)"
echo ""
echo "    ArgoCD:   http://argocd.local"
echo "      Admin:  admin (see .env for password)"
echo ""
echo "    Keycloak: http://keycloak.local"
echo "      Admin:  admin (see .env for password)"
echo ""
echo "  All credentials are in .env"
echo "  Run 'kubectl get applications -n argocd' to check status"
echo "=============================================="
