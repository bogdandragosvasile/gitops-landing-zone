#!/usr/bin/env bash
# Create a Headscale user. Users own nodes; a preauth key is scoped to a user.
#
#   bash scripts/create-user.sh <username>
set -euo pipefail

USER="${1:-}"
if [[ -z "$USER" ]]; then
  echo "Usage: $0 <username>"
  exit 1
fi

POD=$(kubectl -n headscale get pod -l app=headscale -o jsonpath='{.items[0].metadata.name}')
kubectl -n headscale exec "$POD" -- headscale users create "$USER"
kubectl -n headscale exec "$POD" -- headscale users list
