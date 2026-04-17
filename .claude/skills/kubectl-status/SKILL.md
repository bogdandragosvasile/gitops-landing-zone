---
name: kubectl-status
description: Show pod, service, and ingress health for a namespace (or all namespaces). Use when checking deployment status.
allowed-tools: Bash(kubectl *) Bash(argocd *)
---

# Kubernetes Status Check

Show health for namespace `$ARGUMENTS` (or all namespaces if `--all`).

```bash
if [ "$ARGUMENTS" = "--all" ] || [ -z "$ARGUMENTS" ]; then
  echo "=== ArgoCD Applications ==="
  kubectl get applications -n argocd -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
  echo ""
  echo "=== Unhealthy Pods ==="
  kubectl get pods -A | grep -v -E "Running|Completed|NAMESPACE" || echo "(none)"
else
  echo "=== Namespace: $ARGUMENTS ==="
  kubectl get pods,svc,ingress -n "$ARGUMENTS" -o wide
fi
```

Report the results as a structured health summary.
