#!/usr/bin/env bash
# Generate a Headscale pre-auth key. Paste this into Tailscale on the
# phone/laptop (with the custom login server set to http://headscale.local).
#
#   bash scripts/create-preauth.sh <username> [--reusable] [--ephemeral] [--expiry 1h]
set -euo pipefail

USER="${1:-}"
shift || true
if [[ -z "$USER" ]]; then
  echo "Usage: $0 <username> [--reusable] [--ephemeral] [--expiry 1h]"
  exit 1
fi

POD=$(kubectl -n headscale get pod -l app=headscale -o jsonpath='{.items[0].metadata.name}')
kubectl -n headscale exec "$POD" -- headscale preauthkeys create \
  --user "$USER" \
  "$@"
