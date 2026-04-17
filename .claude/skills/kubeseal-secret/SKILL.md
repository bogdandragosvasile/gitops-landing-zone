---
name: kubeseal-secret
description: Seal a Kubernetes Secret using kubeseal. Use when committing secrets to Git.
allowed-tools: Bash(kubeseal *) Bash(kubectl *)
---

# Seal a Secret

Seal the secret YAML at `$ARGUMENTS` using the sealed-secrets controller.

Steps:
1. Validate the input is a valid Secret YAML
2. Seal: `kubeseal --controller-name=sealed-secrets --controller-namespace=kube-system --format=yaml < $ARGUMENTS > ${ARGUMENTS%.yaml}-sealed.yaml`
3. Verify the output is a valid SealedSecret
4. Report the output file path

NEVER commit the plaintext secret to Git — only commit the sealed version.
