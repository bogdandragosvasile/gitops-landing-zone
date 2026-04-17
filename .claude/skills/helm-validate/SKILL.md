---
name: helm-validate
description: Lint and template-validate a Helm chart. Use before deploying chart changes.
allowed-tools: Bash(helm *)
---

# Helm Validate

Validate the Helm chart at path `$ARGUMENTS` (optionally with `-f values.yaml`).

Steps:
1. Lint: `helm lint $ARGUMENTS`
2. Template dry-run: `helm template test $ARGUMENTS --debug`
3. Report any errors or warnings.

If a values file is specified (e.g., `charts/my-chart -f values-dev.yaml`), include it in both commands.
