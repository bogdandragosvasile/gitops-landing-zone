#!/usr/bin/env bash
# Build the industry40 image and import it into every k3d node.
# Runs from this repo's root; no arguments.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER="${K3D_CLUSTER_NAME:-gitops-local}"
ARCH="$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')"
IMAGE="industry40:dev"
TARBALL="/tmp/industry40.tar"

echo "→ docker build --platform linux/$ARCH -t $IMAGE $HERE"
docker build --platform "linux/$ARCH" -t "$IMAGE" "$HERE"

echo "→ docker save $IMAGE"
docker save "$IMAGE" -o "$TARBALL"

# Filter server/agent nodes of THIS cluster (loadbalancer + tools lack ctr)
for node in $(k3d node list -o json 2>/dev/null \
    | python3 -c "import sys,json
for n in json.load(sys.stdin):
    if n.get('role') not in ('server','agent'): continue
    if n.get('runtimeLabels',{}).get('k3d.cluster') != '${CLUSTER}': continue
    print(n['name'])"); do
  echo "→ import into $node"
  docker cp "$TARBALL" "$node:/tmp/industry40.tar"
  docker exec "$node" ctr -n k8s.io images import /tmp/industry40.tar
  docker exec "$node" rm -f /tmp/industry40.tar
done

rm -f "$TARBALL"
echo "✓ $IMAGE imported into all nodes of cluster $CLUSTER"
