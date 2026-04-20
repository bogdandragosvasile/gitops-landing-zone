---
name: netpol-test
description: Test network connectivity between Kubernetes namespaces. Use when verifying NetworkPolicy rules.
allowed-tools: Bash(kubectl *)
---

# Network Policy Test

Test connectivity from namespace `$ARGUMENTS` to key services.

Steps:
1. Create an ephemeral pod in the source namespace:
   ```bash
   kubectl run netpol-test --rm -it --restart=Never -n $ARGUMENTS \
     --image=busybox:latest -- /bin/sh -c \
     "wget -qO- --timeout=5 http://<target-service>.<target-ns>.svc.cluster.local:<port>/ && echo OK || echo BLOCKED"
   ```
2. Test connectivity to:
   - `your-service.your-namespace:8080`
   - `postgres.your-namespace:5432`
   - `argocd-server.argocd:80`
   - `keycloak-keycloakx-http.keycloak:80`
3. Report: which connections succeeded and which were blocked.

NOTE: On Windows/Git Bash, prefix `kubectl exec` / `kubectl run` invocations with `MSYS_NO_PATHCONV=1` when passing Unix paths. Not needed on macOS or native Linux.
