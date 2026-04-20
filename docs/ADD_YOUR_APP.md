# Add Your Own Application

The landing zone is a base platform. You deploy your applications on top by adding an ArgoCD `Application` that points at your own Git repo hosted in the local Gitea.

## TL;DR

1. **Create a repo in Gitea** — `platform/my-app` (web UI or `gitea-api` skill).
2. **Push your K8s manifests** to that repo under `manifests/`.
3. **Add an ArgoCD Application manifest** to `gitops-repo/apps/my-app.yaml` (copy `gitops-repo/apps-examples/my-app.yaml.example`).
4. **Commit + push** the gitops-repo to Gitea. Root app-of-apps picks up the new Application within seconds.
5. **Build your image and import it into k3d** — see `.claude/skills/docker-build-import/SKILL.md`.
6. **Add a hostname** to `/etc/hosts` and the CoreDNS patch (see below).

## Step-by-step

### 1. Create the app source repo

```bash
# Using the Gitea API directly — or the /gitea-api skill:
source .env
curl -sf -u "$GITEA_ADMIN_USER:$GITEA_ADMIN_PASSWORD" \
  -X POST "http://localhost:$GITEA_HTTP_PORT/api/v1/orgs/$GITEA_ORG/repos" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","auto_init":true,"default_branch":"main"}'
```

### 2. Lay out your repo

Minimum:

```
my-app/
├── Dockerfile
└── manifests/
    ├── deployment.yaml
    ├── service.yaml
    └── ingress.yaml      # hostname my-app.local, ingressClassName: traefik
```

Use `imagePullPolicy: Never` and a local tag like `my-app:dev` — no registry required.

### 3. Register the Application

```bash
cd gitops-landing-zone
cp gitops-repo/apps-examples/my-app.yaml.example gitops-repo/apps/my-app.yaml
# edit placeholders: name, repoURL, namespace, path
```

### 4. Push the gitops-repo

```bash
cd gitops-repo
git add apps/my-app.yaml
git commit -m "feat(apps): add my-app"
git push origin main
```

ArgoCD will sync the new Application automatically (webhook — see `scripts/configure-webhooks.sh`) or within the 3 min polling window.

### 5. Build + import the image

```bash
# Manual build + single-arch import (the k3d multi-arch issue is real):
docker build --platform linux/$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') \
  -t my-app:dev ./my-app
docker save my-app:dev -o /tmp/my-app.tar
for n in k3d-gitops-local-server-0 k3d-gitops-local-agent-0 k3d-gitops-local-agent-1; do
  docker cp /tmp/my-app.tar "$n:/tmp/my-app.tar"
  docker exec "$n" ctr -n k8s.io images import /tmp/my-app.tar
  docker exec "$n" rm /tmp/my-app.tar
done
rm /tmp/my-app.tar
```

Or trigger via the `/docker-build-import` Claude Code skill.

### 6. Add a hostname

**Host side** — append to `/etc/hosts`:

```
127.0.0.1 my-app.local
```

**In-cluster side** — if other pods need to reach `my-app.local` (e.g. for OIDC redirects or service-to-service calls), also patch CoreDNS:

```bash
kubectl -n kube-system get cm coredns -o json \
  | jq '.data.NodeHosts += "\n172.20.0.100 my-app.local"' \
  | kubectl apply -f -
kubectl -n kube-system rollout restart deploy coredns
```

## Optional: add an OIDC client for SSO

If your app uses Keycloak for login, add a client registration block to `gitops-repo/manifests/keycloak/configure-job.yaml` following the Gitea/ArgoCD pattern already there. ArgoCD re-runs the PostSync Job idempotently on every sync.

## Optional: add a card to the landing portal

Edit `gitops-repo/manifests/portal/src/index.html` and add a `<a class="card">` entry under the "Your Applications" grid. Then rebuild + import the portal image:

```bash
bash scripts/09b-build-portal.sh
kubectl -n portal rollout restart deploy portal
```

## Optional: add a domain agent

For larger apps managed by the Claude Code agent federation, create `.claude/agents/my-app.md` following the pattern in `.claude/agents/platform-infra.md`. See `CLAUDE.md` for the overall federation model.
