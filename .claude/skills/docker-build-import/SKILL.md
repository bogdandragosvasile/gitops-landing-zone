---
name: docker-build-import
description: Build a Docker image and import it into the k3d cluster. Handles multi-arch manifests via tarball method. Use when building application images.
allowed-tools: Bash(docker *) Bash(k3d *)
---

# Docker Build + k3d Import

Build and import an image. Arguments: `<dockerfile-dir> <image:tag> [--platform linux/<arch>]`

Pass a single-arch `--platform` matching the k3d node arch (host arch: `linux/amd64` on x86_64, `linux/arm64` on Apple Silicon / Colima arm64). Multi-arch manifests break `ctr import`.

Steps:
1. Build: `docker build --platform linux/$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') -t <image:tag> <dockerfile-dir>`
2. Try k3d import first: `k3d image import <image:tag> -c gitops-local`
3. If k3d import fails (multi-arch digest error), use the tarball method:
   ```bash
   docker save <image:tag> -o /tmp/img.tar
   for n in k3d-gitops-local-server-0 k3d-gitops-local-agent-0 k3d-gitops-local-agent-1; do
     docker cp /tmp/img.tar $n:/tmp/img.tar
     docker exec $n ctr -n=k8s.io images import /tmp/img.tar
     docker exec $n rm -f /tmp/img.tar
   done
   rm /tmp/img.tar
   ```
4. Verify image exists on all nodes: `docker exec <node> crictl images | grep <image-name>`

NOTE: On Windows/Git Bash, prefix `docker exec` invocations that use Unix paths with `MSYS_NO_PATHCONV=1`. Not needed on macOS or native Linux.
