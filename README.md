# GitOps Landing Zone

A fully reproducible local GitOps development environment.
Runs on **Windows 11 + Docker Desktop**, **Linux/WSL + Docker Engine**, or **macOS + Colima** (Apple Silicon supported).
Deploy your own applications on top. Everything managed via ArgoCD from a self-hosted Gitea.

![status](https://img.shields.io/badge/status-ready--to--fork-green)
![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS%20(M%E2%80%90series)-blue)
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
Host (Windows 11 / Linux / macOS — Colima on Apple Silicon)
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

| Platform | Runtime | Shell | Extras |
|---|---|---|---|
| **Windows 11** | Docker Desktop (≥16 GB RAM) | Git Bash + elevated PowerShell | hosts file admin rights |
| **Linux / WSL2** | Docker Engine | bash | `sudo` for `/etc/hosts` |
| **macOS (Intel or Apple Silicon)** | Colima (or Docker Desktop) | bash/zsh + Homebrew | `sudo` for `/etc/hosts` |

Common requirements: `git`, `curl`, `openssl`, `envsubst` (macOS: `brew install gettext`), `python3`, and ~30 GB free disk space.

## Quick start

### macOS (Apple Silicon or Intel)

```bash
# 1. Start Colima (one-time; re-run after machine reboots)
colima start --cpu 4 --memory 10 --disk 60
# Apple Silicon with Rosetta (only needed if you plan to run x86_64 images):
#   colima start --cpu 4 --memory 10 --disk 60 --vm-type vz --vz-rosetta

# 2. Clone + configure
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone
cp .env.example .env
# Edit .env and replace every CHANGE_ME_* value with a strong secret:
#   openssl rand -base64 24   # passwords
#   openssl rand -hex 24      # OIDC client secrets
#   openssl rand -base64 48   # Vaultwarden admin token

# 3. Bootstrap
./bootstrap.sh
```

### Linux / WSL2

```bash
cp .env.example .env
# edit .env
./bootstrap.sh
```

### Windows 11

```powershell
# Clone, copy + edit .env as above, then from an ELEVATED PowerShell:
.\bootstrap.ps1
```

Total runtime on first run: ~15-20 minutes.

## What's in the box

```
gitops-landing-zone/
├── bootstrap.sh                  # Entry point for Linux / WSL / macOS (runs scripts/bootstrap.sh)
├── bootstrap.ps1                 # Entry point for Windows (elevated PowerShell wrapper)
├── teardown.sh / teardown.ps1    # Matching teardown entry points
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

See [`docs/ADD_YOUR_APP.md`](docs/ADD_YOUR_APP.md) for the full recipe. In short:

1. Copy the template `gitops-repo/apps-examples/my-app.yaml.example` to `gitops-repo/apps/my-app.yaml` and edit the name/URL/path.
2. Create the matching repo (`platform/my-app`) in the local Gitea and push your Kubernetes manifests.
3. `git push` the gitops-repo — root app-of-apps materializes your Application within seconds.
4. Build + import the image (`docker build ... | ctr -n k8s.io images import`) — see `.claude/skills/docker-build-import/SKILL.md`.
5. Add `127.0.0.1 my-app.local` to `/etc/hosts` and, if needed, patch CoreDNS.

For a larger app with its own agent, drop a file into `.claude/agents/my-app.md` following the pattern in `.claude/agents/platform-infra.md`.

## Example apps (separate repo)

The BankOffer AI + CareerForge workloads that originally lived here have been moved to the sibling repo [`my-testing-apps`](https://github.com/bogdandragosvasile/my-testing-apps). Use that as a reference for a non-trivial multi-service deployment on top of the landing zone; this repo stays a clean base platform.

## Service URLs

After bootstrap, these resolve to `127.0.0.1` via dnsmasq or hosts file:

| URL | Service | Credentials |
|---|---|---|
| http://portal.local | Landing portal (overview of everything) | — |
| http://argocd.local | ArgoCD UI | `admin` / see `.env` (`ARGOCD_ADMIN_PASSWORD`) |
| http://gitea.local:3000 | Gitea web + API | `gitea_admin` / see `.env` (`GITEA_ADMIN_PASSWORD`) |
| http://keycloak.local | Keycloak admin | `admin` / see `.env` (`KEYCLOAK_ADMIN_PASSWORD`) |
| http://grafana.local | Grafana dashboards | `admin` / see `.env` (`GRAFANA_ADMIN_PASSWORD`) |
| http://prometheus.local | Prometheus UI | — |
| https://localhost:8443 | Vaultwarden | your master password (admin token in `.env`) |

For SSO flows, log in via Keycloak in the `gitops` realm as `dev` / `dev`.

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

```bash
./teardown.sh       # Linux / WSL / macOS
# — or —
.\teardown.ps1      # Windows (elevated PowerShell)
```

Stops the cluster and compose stack. Volumes preserved unless you `docker volume rm ...`.

## Graceful restart

**Stop:** `k3d cluster stop gitops-local` → `docker compose -f docker-compose/docker-compose.yml stop`

**Start:** `docker compose ... start` → `k3d cluster start gitops-local` → re-patch CoreDNS NodeHosts for `gitea.local` (wiped on every cluster restart).

**macOS note:** after a machine reboot, also run `colima start` before the compose/k3d steps.

## macOS / Apple Silicon specifics

- **Images built on arm64 stay arm64** — `scripts/09b-build-portal.sh` and `scripts/09c-build-app-images.sh` auto-detect the host arch and pass `--platform linux/arm64` on Apple Silicon, `linux/amd64` on x86_64. Multi-arch images are avoided because `ctr import` on k3d nodes cannot handle multi-arch manifests.
- **Bitnami images** (e.g. `bitnami/postgresql:16`) are pulled with an explicit single-arch `--platform` for the same reason.
- **dnsmasq does not bind :53 on the host** — Colima cannot reliably forward privileged ports, and the `docker-compose.linux.yml` override (applied automatically on macOS + Linux + WSL) removes that port binding. Host-side `*.local` resolution uses `/etc/hosts`; in-cluster DNS uses dnsmasq at `172.20.0.2:53`.
- **GNU grep recommended** — `brew install grep` gives you `ggrep` with `-P` support; the pre-commit secret scanner auto-detects it. Without it the scanner falls back to skipping Perl-regex patterns.
- **Docker socket** — Colima exposes docker at `~/.colima/default/docker.sock`; `docker context` auto-selects it. The `gitea-runner` container mount of `/var/run/docker.sock` is resolved inside the Colima VM, so no change is needed on the host.

## Contributing

This is a personal model / template. Fork it, rip out what you don't need, add your own applications.

## License

MIT
