---
name: cluster-health
description: Comprehensive cluster health verification — nodes, pods, ArgoCD apps, MetalLB, ingresses. Use after deployments or to diagnose issues.
allowed-tools: Bash(kubectl *) Bash(curl *)
---

# Cluster Health Check

Run a comprehensive health verification of the entire landing zone.

Checks:
1. **Nodes**: `kubectl get nodes` — all must be `Ready`
2. **ArgoCD apps**: `kubectl get applications -n argocd` — all must be `Synced`+`Healthy`
3. **Unhealthy pods**: `kubectl get pods -A | grep -v Running|Completed` — must be empty
4. **MetalLB**: `kubectl get ipaddresspool -n metallb-system` — pool must exist
5. **Ingresses**: `kubectl get ingress -A` — all must have an ADDRESS assigned
6. **Service endpoints**: Test key URLs with curl:
   - `curl -sf -H "Host: argocd.local" http://127.0.0.1/` (ArgoCD)
   - `curl -sf http://gitea.local:3000/` (Gitea)
   - `curl -sf -H "Host: keycloak.local" http://127.0.0.1/` (Keycloak)
   - `curl -sf -H "Host: grafana.local" http://127.0.0.1/login` (Grafana)
   - `curl -sf -H "Host: portal.local" http://127.0.0.1/` (Portal)

Report: PASS/FAIL per check, with details on any failures.
