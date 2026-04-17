# GitOps Landing Zone

A fully reproducible local GitOps development environment for Windows 11 + Docker Desktop.
Deploy your own applications on top. Everything managed via ArgoCD from a self-hosted Gitea.

![status](https://img.shields.io/badge/status-ready--to--fork-green)
![platform](https://img.shields.io/badge/platform-Windows%2011-blue)
![stack](https://img.shields.io/badge/stack-k3d%20%2B%20ArgoCD%20%2B%20Gitea%20%2B%20Keycloak-red)

---

## What you get

| Service | Purpose |
|---|---|
| **k3d** | 3-node Kubernetes cluster running inside Docker Desktop |
| **Gitea** | Self-hosted Git server + container registry + Actions CI runner |
| **ArgoCD** | GitOps controller with app-of-apps pattern |
| **Keycloak** | SSO / OIDC for Gitea + ArgoCD |
| **cert-manager** | TLS certificate automation |
| **Sealed Secrets** | Encrypt Kubernetes secrets for Git storage |
| **MetalLB** | LoadBalancer for the local Docker network |
| **Prometheus + Grafana + Loki** | Full observability stack with dashboards |
| **Vaultwarden** | Bitwarden-compatible password manager |
| **dnsmasq** | Local DNS resolver for `*.local` domains |
| **Landing Portal** | Unified entry point at `portal.local` with dark/light theme |
| **AI Agent Federation** | 6 Claude Code agents + 12 skills for infrastructure management |

All traffic routed through Traefik ingress with `*.local` hostnames.

## Architecture

```
Host (Windows 11 + Docker Desktop)
│
├── Docker Network: gitops (172.20.0.0/24)
│   ├── gitea + gitea-db + gitea-runner
│   ├── vaultwarden (HTTPS 8443)
│   └── dnsmasq (*.local → 127.0.0.1)
│
└── k3d cluster (1 server + 2 agents)
    ├── Traefik ingress (ports 80/443)
    ├── MetalLB (172.20.0.100-150)
    ├── ArgoCD (app-of-apps)
    ├── Keycloak + PostgreSQL
    ├── cert-manager, sealed-secrets
    ├── Prometheus + Grafana + Loki + Promtail
    └── portal.local (landing page)
```

## Prerequisites

- **Windows 11** with Docker Desktop (>=16 GB RAM allocated)
- **Git Bash** (Git for Windows)
- **PowerShell** (for the elevated wrapper)
- ~30 GB free disk space
- Admin rights (to edit the Windows hosts file on first run)

## Quick start

```powershell
# 1. Clone the repo
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone

# 2. Copy the env template and fill in passwords (openssl rand -base64 24)
cp .env.example .env
# Edit .env with your favorite editor

# 3. Bootstrap from an ELEVATED PowerShell
.\bootstrap.ps1
```

Total runtime on first run: ~15-20 minutes.

## What's in the box

```
gitops-landing-zone/
├── bootstrap.ps1                 # Entry point (elevated PowerShell wrapper)
├── .env.example                  # Template — copy to .env and fill in
├── .gitignore                    # Prevents committing .env and other secrets
├── .gitleaks.toml                # Secret scanner config for pre-commit hooks
├── CLAUDE.md                     # Agent federation overview
├── REPRODUCTION.md               # Detailed reproduction guide
├── docker-compose/
│   ├── docker-compose.yml        # Gitea, gitea-db, gitea-runner, vaultwarden, dnsmasq
│   └── dnsmasq.conf              # *.local wildcard DNS
├── k3d/
│   └── k3d-config.yaml           # 1 server + 2 agents, MetalLB, Traefik hostPort
├── scripts/
│   ├── bootstrap.sh              # Master orchestrator (11 phases)
│   ├── teardown.sh               # Full cleanup
│   ├── 00-prerequisites.sh       # Install k3d, helm, argocd CLI
│   ├── 01-create-network.sh      # Docker network
│   ├── 02-start-gitea.sh         # Start compose stack
│   ├── 03-configure-gitea.sh     # Admin user + org + runner
│   ├── 04-create-k3d-cluster.sh  # Cluster + Traefik + CoreDNS
│   ├── 05-install-metallb.sh     # LoadBalancer
│   ├── 06-install-argocd.sh      # ArgoCD Helm install
│   ├── 07-push-gitops-repo.sh    # Push manifests to Gitea
│   ├── 08-configure-argocd-repo.sh  # Register Gitea in ArgoCD
│   ├── 09-apply-app-of-apps.sh   # Activate root Application
│   ├── 10-configure-oidc.sh      # Keycloak realms + clients
│   ├── configure-dns.sh          # Windows NRPT rule for *.local
│   ├── configure-webhooks.sh     # Gitea → ArgoCD push webhooks
│   ├── backup.sh                 # pg_dump all + Keycloak export + Vaultwarden
│   ├── restore.sh                # Restore from a backup archive
│   ├── install-hooks.sh          # Install pre-commit hooks in all repos
│   └── pre-commit-hook.sh        # Secret scan + YAML lint + helm lint
├── gitops-repo/                  # Pushed to Gitea as platform/gitops-infra
│   ├── apps/                     # 11 ArgoCD Applications (base platform)
│   ├── manifests/                # Raw K8s manifests + helm values
│   └── .gitea/workflows/
│       └── validate.yaml         # YAML lint + helm lint + dry-run
└── .claude/                      # AI agent federation
    ├── agents/                   # 6 agent definitions
    ├── skills/                   # 12 reusable slash commands
    └── settings.json             # Permissions + audit hooks
```

## Extending with your own applications

1. **Create a repo for your app** (e.g., `myapp-platform`):
   ```bash
   # Push to your self-hosted Gitea (not GitHub)
   git remote add origin http://gitea.local:3000/platform/myapp-platform.git
   git push -u origin main
   ```

2. **Add an ArgoCD Application** in `gitops-repo/apps/myapp.yaml`:
   ```yaml
   apiVersion: argoproj.io/v1alpha1
   kind: Application
   metadata:
     name: myapp
     namespace: argocd
   spec:
     project: dev
     source:
       repoURL: http://gitea:3000/platform/myapp-platform.git
       targetRevision: main
       path: manifests
     destination:
       server: https://kubernetes.default.svc
       namespace: myapp
     syncPolicy:
       automated: {selfHeal: true, prune: true}
       syncOptions: [CreateNamespace=true]
   ```

3. **Add a domain agent** in `.claude/agents/myapp.md` following the pattern in `platform-infra.md`.

4. Push both repos. Root app-of-apps picks up the new Application automatically.

## Service URLs

After bootstrap, these resolve to `127.0.0.1` via dnsmasq or hosts file:

| URL | Service |
|---|---|
| http://portal.local | Landing portal (overview of everything) |
| http://argocd.local | ArgoCD UI |
| http://gitea.local:3000 | Gitea web + API |
| http://keycloak.local | Keycloak admin |
| http://grafana.local | Grafana dashboards |
| http://prometheus.local | Prometheus UI |
| https://localhost:8443 | Vaultwarden |

## Security

- **No secrets in Git**: `.env` is gitignored. Kubernetes secrets use Sealed Secrets.
- **Pre-commit hooks**: gitleaks scans staged files before every commit.
- **Network policies**: 27 policies enforce deny-all-ingress + explicit Traefik allowlists.
- **Vaultwarden**: self-hosted credentials vault (HTTPS with self-signed cert).
- **Keycloak OIDC**: SSO for Gitea and ArgoCD.

## Operational scripts

| Script | Purpose |
|---|---|
| `scripts/backup.sh` | Backup Gitea DB + Keycloak realms + Vaultwarden + `.env` → `backups/<timestamp>.tar.gz` |
| `scripts/restore.sh <archive>` | Restore from a backup |
| `scripts/configure-dns.sh` | Set up local DNS (avoid hosts file pain) |
| `scripts/configure-webhooks.sh` | Instant ArgoCD sync on push (no 3-min polling) |
| `scripts/install-hooks.sh` | Pre-commit secret + YAML + helm linting |

## AI Agent Federation

6 Claude Code agents manage the landing zone:

```
orchestrator
├── platform-infra
│   ├── cluster-lifecycle
│   └── gitea-argocd
├── observability
└── security
```

Plus 12 reusable skills: `/kubectl-status`, `/argocd-sync`, `/docker-build-import`,
`/helm-validate`, `/kubeseal-secret`, `/bootstrap-phase`, `/gitea-api`, `/keycloak-admin`,
`/kustomize-build`, `/grafana-dashboard`, `/cluster-health`, `/netpol-test`.

See [CLAUDE.md](CLAUDE.md) and the [federation page](http://portal.local/federation.html) after bootstrap.

## Teardown

```powershell
.\teardown.ps1
```

Stops the cluster and compose stack. Volumes preserved unless you `docker volume rm ...`.

## Graceful restart

**Stop:** `k3d cluster stop gitops-local` → `docker compose -f docker-compose/docker-compose.yml stop`

**Start:** `docker compose ... start` → `k3d cluster start gitops-local` → re-patch CoreDNS NodeHosts for `gitea.local` (wiped on every cluster restart).

## Contributing

This is a personal model / template. Fork it, rip out what you don't need, add your own applications.

## License

MIT
