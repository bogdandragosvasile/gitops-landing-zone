---
name: argocd-sync
description: Sync an ArgoCD application and wait until it reaches Synced+Healthy. Use when deploying GitOps changes.
allowed-tools: Bash(kubectl *)
---

# ArgoCD Sync

Sync the ArgoCD application named `$ARGUMENTS` and wait for it to become Healthy.

Steps:
1. Hard-refresh the application: `kubectl annotate application $ARGUMENTS -n argocd argocd.argoproj.io/refresh=hard --overwrite`
2. Clear any stuck operation: `kubectl patch application $ARGUMENTS -n argocd --type='merge' -p='{"operation":null}'`
3. Trigger sync: `kubectl patch application $ARGUMENTS -n argocd --type='json' -p='[{"op":"replace","path":"/operation","value":{"initiatedBy":{"username":"admin"},"sync":{"revision":"HEAD"}}}]'`
4. Poll until `status.sync.status=Synced` AND `status.health.status=Healthy` (timeout 120s)
5. If the operation fails, report the error message from `status.operationState.message`

Report: app name, sync status, health status, and any errors.
