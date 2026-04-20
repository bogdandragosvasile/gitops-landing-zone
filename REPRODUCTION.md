# Reproduction Guide

Step-by-step instructions to reproduce the landing zone on Windows 11, Linux/WSL, or macOS (Apple Silicon / Intel).

## Prerequisites

### Windows 11
- Docker Desktop (≥16 GB RAM allocated to WSL2 / Docker)
- Git Bash (bundled with Git for Windows)
- PowerShell (admin rights for first run)
- ~30 GB free disk space

### Linux / WSL2
- Docker Engine running (`sudo systemctl start docker`) with ≥16 GB available
- `bash`, `git`, `curl`, `openssl`, `gettext-base` (`envsubst`), `python3`
- `sudo` access (for `/etc/hosts` + hosts-file writes)
- ~30 GB free disk space

### macOS (Intel or Apple Silicon)
- [Homebrew](https://brew.sh) (`/opt/homebrew/bin` on Apple Silicon, `/usr/local/bin` on Intel)
- [Colima](https://github.com/abiosoft/colima): `brew install colima docker docker-compose`
- `brew install gettext git curl openssl python@3.12` (plus optional `grep` for `ggrep`)
- `sudo` access (for `/etc/hosts`)
- ~30 GB free disk space

Start Colima before running bootstrap:

```bash
# Default (recommended for Apple Silicon — everything runs arm64 natively):
colima start --cpu 4 --memory 10 --disk 60

# Apple Silicon with Rosetta (only if you need to run x86_64 images):
colima start --cpu 4 --memory 10 --disk 60 --vm-type vz --vz-rosetta
```

## One-shot bootstrap

### Windows

```powershell
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone
cp .env.example .env
# Fill in passwords (openssl rand -base64 24 for each)

# Run from ELEVATED PowerShell
.\bootstrap.ps1
```

### Linux / WSL / macOS

```bash
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone
cp .env.example .env
# Fill in passwords (openssl rand -base64 24 for each)

./bootstrap.sh
```

`bootstrap.sh` / `bootstrap.ps1` both delegate to `scripts/bootstrap.sh`, which runs these phases:

| # | Script | Purpose |
|---|---|---|
| 0 | `setup-hosts.sh` | Windows hosts file entries for `*.local` |
| 1 | `00-prerequisites.sh` | Install k3d, helm, argocd CLI |
| 2 | `01-create-network.sh` | Docker network `gitops` (172.20.0.0/24) |
| 3 | `02-start-gitea.sh` | docker-compose up (gitea + dnsmasq + vaultwarden) |
| 4 | `03-configure-gitea.sh` | Admin user + org + runner registration |
| 5 | `04-create-k3d-cluster.sh` | 3-node cluster, Traefik hostPort, CoreDNS patch |
| 6 | `05-install-metallb.sh` | LoadBalancer with IP pool 172.20.0.100-150 |
| 7 | `06-install-argocd.sh` | ArgoCD Helm install, dev project |
| 8 | `07-push-gitops-repo.sh` | Push gitops-repo to Gitea |
| 9 | `08-configure-argocd-repo.sh` | Register Gitea as ArgoCD source |
| 10 | `09-apply-app-of-apps.sh` | Activate root Application |
| 11 | `10-configure-oidc.sh` | Keycloak realms + OIDC clients |

After phase 10, the root app-of-apps materializes all 11 child applications automatically.

## Post-bootstrap

### Configure DNS (optional but recommended)

```bash
bash scripts/configure-dns.sh
```

Sets a Windows NRPT rule routing `*.local` to the local dnsmasq container. Eliminates hosts file pain.

### Install pre-commit hooks

```bash
bash scripts/install-hooks.sh
```

Scans staged files for secrets (via gitleaks patterns), validates YAML, lints any modified Helm charts. Installs into all local repos.

### Configure instant GitOps sync

```bash
bash scripts/configure-webhooks.sh
```

Adds a Gitea webhook for each repo pointing at ArgoCD's `/api/webhook`. Push triggers sync within seconds instead of 3-minute polling.

### Set up Vaultwarden

Open `https://localhost:8443`, create an account with a strong master password, then use the Bitwarden CLI to populate your credentials:

```bash
export NODE_TLS_REJECT_UNAUTHORIZED=0
bw config server https://localhost:8443
bw login <email> <master-password>
```

## Service URLs

| URL | Service | Credentials |
|---|---|---|
| http://portal.local | Landing portal | none |
| http://argocd.local | ArgoCD | `admin` / see `.env` |
| http://gitea.local:3000 | Gitea | `gitea_admin` / see `.env` |
| http://keycloak.local | Keycloak admin | `admin` / see `.env` |
| http://grafana.local | Grafana | `admin` / see `.env` |
| http://prometheus.local | Prometheus | none |
| https://localhost:8443 | Vaultwarden | your master password |

## Security

### Secrets at rest

| Layer | Tool | How |
|---|---|---|
| `.env` operator creds | gitignored + `.env.example` template | Never committed |
| K8s secrets in Git | Sealed Secrets (kubeseal) | Encrypt, commit sealed YAML, controller decrypts |
| Runtime credentials | Vaultwarden | Personal password manager for humans |
| Pre-commit scanning | gitleaks + custom hook | Scans every staged commit |

### Network policies

27 NetworkPolicy resources across 11 namespaces enforce deny-all-ingress by default with explicit Traefik allowlists, intra-namespace rules, and Prometheus scrape access.

File: `gitops-repo/manifests/local-ingress/network-policies.yaml`

### Resource governance

All workloads have explicit resource requests and limits. See `gitops-repo/manifests/*/values.yaml` for per-service values.

## Observability

### Grafana dashboards

One pre-built dashboard auto-loaded via sidecar (ConfigMap with `grafana_dashboard: "1"`):

| Dashboard | What it shows |
|---|---|
| Landing Zone Overview | Pod counts, node CPU/memory, pod restart rates, namespace breakdown |

Add your own by creating a ConfigMap in the `monitoring` namespace with the same label.

### Prometheus alerts

4 alert groups defined in `gitops-repo/manifests/monitoring/alerts.yaml`:

| Group | Alerts |
|---|---|
| pod-health | CrashLoopBackOff, pod not ready >5m, OOMKilled, replica mismatch |
| node-health | Node not ready, disk/memory pressure |
| argocd | App out of sync >10m, app degraded |
| service-health | High restart rate (>3 in 15m), CPU throttling |

Alertmanager is disabled by default. Enable in `gitops-repo/apps/monitoring.yaml` and wire to your notification channel.

## Agent Federation

See [CLAUDE.md](CLAUDE.md) for the 6-agent hierarchy and 12 skills.

## Teardown

```bash
./teardown.sh       # Linux / WSL / macOS
# — or —
.\teardown.ps1      # Windows (elevated PowerShell)
```

Deletes the k3d cluster and stops the compose stack. Volumes preserved. To wipe everything including data: `docker compose down -v && docker volume prune`.

## Graceful restart

**macOS only:** start Colima first if the machine was rebooted: `colima start`.

**Stop:**
1. `k3d cluster stop gitops-local`
2. `docker compose -f docker-compose/docker-compose.yml stop`

**Start:**
1. `docker compose -f docker-compose/docker-compose.yml start`
2. `k3d cluster start gitops-local`
3. Re-patch CoreDNS NodeHosts for `gitea.local` (wiped on every restart):
   ```bash
   GITEA_IP=$(docker inspect gitea --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
   kubectl -n kube-system get cm coredns -o json | \
     sed "s|\"NodeHosts\": \"|\"NodeHosts\": \"$GITEA_IP gitea.local\\n|" | \
     kubectl apply -f -
   kubectl -n kube-system rollout restart deploy coredns
   ```

## Verification

All ArgoCD applications should be `Synced` + `Healthy`:

```bash
kubectl get applications -n argocd
# or via CLI:
argocd app list
```

Zero unhealthy pods cluster-wide:

```bash
kubectl get pods -A | grep -v -E "Running|Completed|NAMESPACE"
# (no output expected)
```

Full health check via Claude Code skill:

```
/cluster-health
```
