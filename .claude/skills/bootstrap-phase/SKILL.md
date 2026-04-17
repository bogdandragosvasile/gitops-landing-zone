---
name: bootstrap-phase
description: Run a specific bootstrap phase script (0-12). Use when provisioning or reprovisioning infrastructure.
allowed-tools: Bash(bash *) Bash(source *) Read
---

# Run Bootstrap Phase

Run bootstrap phase `$ARGUMENTS` (a number 0-12 or script name).

Steps:
1. Source the environment: `source ${AI_LOCAL_DEV:-$HOME/gitops-landing-zone}/.env`
2. Source common library: `source ${AI_LOCAL_DEV:-$HOME/gitops-landing-zone}/scripts/lib/common.sh`
3. Run the phase script: `bash ${AI_LOCAL_DEV:-$HOME/gitops-landing-zone}/scripts/<script>.sh`
4. Report success or failure with relevant output.

Phase mapping:
- 0: `00-prerequisites.sh`
- 1: `01-create-network.sh`
- 2: `02-start-gitea.sh`
- 3: `03-configure-gitea.sh`
- 4: `04-create-k3d-cluster.sh`
- 5: `05-install-metallb.sh`
- 6: `06-install-argocd.sh`
- 7: `07-push-gitops-repo.sh`
- 8: `08-configure-argocd-repo.sh`
- 9: `09-apply-app-of-apps.sh`
- 10: `10-configure-oidc.sh`
- 11: `11-build-and-import-images.sh`
- 12: `12-seed-data.sh`
