---
name: kustomize-build
description: Build and validate a kustomize overlay. Use when modifying kustomize manifests.
allowed-tools: Bash(kubectl *) Bash(kustomize *)
---

# Kustomize Build

Build the kustomize overlay at `$ARGUMENTS` and validate.

Steps:
1. Build: `kubectl kustomize $ARGUMENTS`
2. Validate: pipe output to `kubectl apply --dry-run=client -f -`
3. Report any errors, warnings, or the count of rendered resources.

Common overlay: `manifests/my-app/kustomize/overlays/dev`
