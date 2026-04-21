# industry40

A tiny nginx-served HTML page about the history of the four industrial revolutions, deployed on the [gitops-landing-zone](https://github.com/bogdandragosvasile/gitops-landing-zone) via the app-of-apps pattern.

## Layout

```
industry40/
├── Dockerfile        # nginx:alpine + COPY index.html
├── index.html        # single-file self-contained page
├── build.sh          # docker build + ctr -n k8s.io images import on every k3d node
└── manifests/
    ├── namespace.yaml
    ├── deployment.yaml
    ├── service.yaml
    └── ingress.yaml
```

## How it's wired

This repo holds the Kubernetes manifests and image source; the `Application` CR that tells ArgoCD to sync it lives in the **gitops-infra** repo at `apps/industry40.yaml`. The root app-of-apps Application (`apps/root-app.yaml` in gitops-infra) discovers it automatically.

```
gitops-infra/apps/root-app.yaml   →   gitops-infra/apps/industry40.yaml
                                          │
                                          │ repoURL
                                          ▼
                                  platform/industry40  (this repo)
                                          │
                                          │ path: manifests/
                                          ▼
                                  namespace → deployment → service → ingress
```

## Deploying

Once registered in ArgoCD (see gitops-infra `argocd/values.yaml`):

```bash
# 1. Build + import the image (runs once after first sync, or whenever index.html changes)
bash build.sh

# 2. Everything else happens via GitOps — push this repo to Gitea and ArgoCD syncs.
```

Then open [http://industry40.local](http://industry40.local).

## Development

Edit `index.html`, then `bash build.sh && kubectl -n industry40 rollout restart deploy industry40`.
