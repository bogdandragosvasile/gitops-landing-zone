# GitOps Landing Zone — User Manual

> The same content is served at [http://portal.local/manual.html](http://portal.local/manual.html) after bootstrap. This file is the offline / GitHub-readable copy for developers who want to skim before they spin the platform up.

## Table of contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Quick start](#3-quick-start)
4. [Architecture](#4-architecture)
5. [Bootstrap phases](#5-bootstrap-phases)
6. [Service URLs & credentials](#6-service-urls--credentials)
7. [Vaultwarden import](#7-vaultwarden-import)
8. [Single sign-on (Keycloak)](#8-single-sign-on-keycloak)
9. [Deploy your own application](#9-deploy-your-own-application)
10. [Headscale VPN (mobile demos)](#10-headscale-vpn-mobile-demos)
11. [Lifecycle](#11-lifecycle)
    - [Resume after stop](#111-resume-after-stop)
    - [Restart (manual)](#112-restart-manual)
    - [Teardown](#113-teardown)
    - [Backup & restore](#114-backup--restore)
12. [Observability](#12-observability)
13. [Security](#13-security)
14. [AI agent federation](#14-ai-agent-federation)
15. [Platform-specific notes](#15-platform-specific-notes)
    - [macOS / Colima](#151-macos--colima)
    - [Linux / WSL2](#152-linux--wsl2)
    - [Windows](#153-windows)
16. [Troubleshooting](#16-troubleshooting)
17. [Extending further](#17-extending-further)

---

## 1. Overview

The **GitOps Landing Zone** is a fully reproducible local Kubernetes platform with SSO, GitOps, observability, and secret management — everything managed via ArgoCD from a self-hosted Gitea, running entirely on your laptop.

This manual covers daily-use operations. For architecture decisions and upstream component documentation, follow the links in each section.

If you're new to the platform, the fastest path is:

1. Satisfy the [prerequisites](#2-prerequisites) for your host OS.
2. Run [the quick-start](#3-quick-start) — one command.
3. Open [the landing portal](http://portal.local) and follow the links.
4. Import [credentials into Vaultwarden](#7-vaultwarden-import).

---

## 2. Prerequisites

The same `./bootstrap.sh` (or `.\bootstrap.ps1` on Windows) works on all three host families. Pick yours below.

### Linux / WSL2

| Component | Notes |
|---|---|
| Docker Engine 20.x+ | `sudo systemctl start docker`, ≥16 GB memory available |
| `bash`, `git`, `curl`, `openssl` | standard dev-box packages |
| `envsubst` (`gettext-base`) | `sudo apt-get install -y gettext-base` |
| `python3` | used by the bootstrap for JSON/YAML tooling |
| `sudo` | needed for `/etc/hosts` on first run |
| ~30 GB free disk | |

### macOS (Intel or Apple Silicon)

| Component | Notes |
|---|---|
| [Homebrew](https://brew.sh) | required — provides everything else |
| [Colima](https://github.com/abiosoft/colima) + docker CLI | `brew install colima docker docker-compose` |
| git, curl, openssl, gettext, python@3.12 | `brew install git curl openssl gettext python@3.12` |
| grep (optional — GNU) | `brew install grep` — provides `ggrep` with `-P` regex for the pre-commit hook |
| ~30 GB free disk | |

> **Tip:** On 16 GB Macs, start Colima with balanced resources so macOS still has headroom:
>
> ```bash
> colima start --cpu 6 --memory 10 --disk 60
> ```
>
> Don't give Colima more than ~10 GB — the stack fits, and leaving 6 GB to macOS prevents swap thrashing during Helm installs and Docker builds.

### Windows 11

| Component | Notes |
|---|---|
| Docker Desktop | ≥16 GB RAM allocated to WSL2 |
| Git for Windows (Git Bash) | the PowerShell wrapper delegates to it |
| Elevated PowerShell | required for `/etc/hosts` write on first run |
| ~30 GB free disk | |

---

## 3. Quick start

### Linux / WSL2 / macOS

```bash
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone

# macOS only: ensure Colima is up
colima start --cpu 6 --memory 10 --disk 60

./bootstrap.sh
```

### Windows 11

```powershell
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone

# From an ELEVATED PowerShell:
.\bootstrap.ps1
```

First run: **15–25 minutes**. Re-runs are idempotent — every phase detects existing state and skips gracefully.

> **No `.env` editing required.** The bootstrap calls `scripts/gen-env.sh` if `.env` is missing and fills it with URL-safe random secrets. If you want to inspect or regenerate, run `./scripts/gen-env.sh --force`.

---

## 4. Architecture

```
Host (Linux / macOS / Windows)
│
├── Docker Network: gitops (172.20.0.0/24)
│   ├── gitea + gitea-db + gitea-runner
│   ├── vaultwarden (HTTPS 8443)
│   └── dnsmasq (*.local → 127.0.0.1, in-cluster only)
│
└── k3d cluster (1 server + 2 agents)
    ├── Traefik ingress (hostPorts 80 / 443)
    ├── MetalLB (172.20.0.100-150)
    ├── ArgoCD (app-of-apps — 13 child Applications)
    ├── Keycloak + PostgreSQL (SSO for Gitea + ArgoCD)
    ├── cert-manager, sealed-secrets
    ├── Prometheus + Grafana + Loki + Promtail
    ├── Headscale (self-hosted Tailscale control plane)
    ├── Industry 4.0 demo app
    └── Landing Portal (portal.local)
```

Service traffic is routed through Traefik with `*.local` hostnames. These resolve to `127.0.0.1` via `/etc/hosts`; in-cluster DNS is served by dnsmasq at `172.20.0.2:53`.

---

## 5. Bootstrap phases

`./bootstrap.sh` runs the following in order. Each phase is independently runnable via `bash scripts/<phase>.sh`.

| # | Script | What it does |
|---|---|---|
| 0 | `gen-env.sh` | Generates `.env` with URL-safe random secrets (skipped if file exists) |
| 1 | `setup-hosts.sh` | Writes `*.local` entries to `/etc/hosts` (GUI prompt on macOS without TTY) |
| 2 | `00-prerequisites.sh` | Installs k3d, helm, kubectl, argocd, kubeseal, envsubst; tunes Colima inotify + DNS; validates `.env` secrets |
| 3 | `01-create-network.sh` | Creates the `gitops` Docker network (172.20.0.0/24) |
| 4 | `01b-ensure-certs.sh` | Generates the Vaultwarden self-signed TLS cert if missing |
| 5 | `02-start-gitea.sh` | Brings up gitea + gitea-db + vaultwarden + dnsmasq |
| 6 | `03-configure-gitea.sh` | Creates admin user + org + registers the Actions runner |
| 7 | `04-create-k3d-cluster.sh` | Creates the 3-node k3d cluster, patches DNS on macOS, waits for kube-system |
| 8 | `05-install-metallb.sh` | Installs MetalLB and the IP address pool |
| 9 | `06-install-argocd.sh` | Installs ArgoCD via Helm, patches admin password via bcrypt |
| 10 | `07-push-gitops-repo.sh` | Renders gitops-repo into a temp dir, pushes to Gitea (source stays templated) |
| 10b | `07b-mirror-app-repos.sh` | Mirrors every `app-repos/*` directory into `platform/<name>` on Gitea |
| 11 | `08-configure-argocd-repo.sh` | Registers the Gitea repo secret + restarts repo-server |
| 12 | `09-apply-app-of-apps.sh` | Applies the root Application; waits for critical children (cert-manager, keycloak) |
| 13 | `09b-build-portal.sh` | Builds + imports the landing portal image into k3d nodes |
| 13b | `09c-build-demo-images.sh` | Runs `app-repos/*/build.sh` where present (builds demo-app images) |
| 14 | `10-configure-oidc.sh` | Runs the Keycloak realm/client/user bootstrap Job; wires Gitea OIDC |
| 15 | `11-vaultwarden-import.sh` | Installs `bw` CLI, generates `vaultwarden-import.json` from `.env` |

Claude Code users can invoke any phase via the `/bootstrap-phase <number>` skill.

---

## 6. Service URLs & credentials

After bootstrap, open the [landing portal](http://portal.local) for a clickable overview. The table below lists canonical URLs and where each credential lives.

| Service | URL | User | Where to find the password |
|---|---|---|---|
| Landing portal | `http://portal.local` | — | — |
| ArgoCD | `http://argocd.local` | `admin` | `.env` → `ARGOCD_ADMIN_PASSWORD` |
| Gitea | `http://gitea.local:3000` | `gitea_admin` | `.env` → `GITEA_ADMIN_PASSWORD` |
| Keycloak admin | `http://keycloak.local` | `admin` | `.env` → `KEYCLOAK_ADMIN_PASSWORD` |
| Grafana | `http://grafana.local` | `admin` | `.env` → `GRAFANA_ADMIN_PASSWORD` |
| Prometheus | `http://prometheus.local` | — | — |
| Vaultwarden | `https://localhost:8443` | (your master pw) | `.env` → `VAULTWARDEN_ADMIN_TOKEN` (admin panel only) |
| Headscale (API) | `http://headscale.local` | — | pre-auth keys via CLI |
| Industry 4.0 demo | `http://industry40.local` | — | — |

### Viewing a password quickly

```bash
grep -E '^(.*_PASSWORD|.*_TOKEN|.*_USER)=' .env
```

> **Warning:** Treat `.env` as sensitive. It's gitignored, but anything running as your user can read it. If it leaks, regenerate everything with `./scripts/gen-env.sh --force` and re-bootstrap.

---

## 7. Vaultwarden import

Bootstrap phase 15 generates `vaultwarden-import.json` in the repo root — a Bitwarden-compatible JSON with every platform credential pre-filled (8 logins + 3 secure notes under a **GitOps Landing Zone** folder).

### Option A — Web UI

1. Open `https://localhost:8443` (accept the self-signed cert).
2. Click **Create account**, set a strong master password.
3. Log in. Go to **Tools → Import data**.
4. File format: **Bitwarden (json)**.
5. Select `vaultwarden-import.json`. Click **Import data**.

### Option B — `bw` CLI

```bash
# Self-signed cert — tell bw to skip verification
export NODE_TLS_REJECT_UNAUTHORIZED=0
bw config server https://localhost:8443

# Create the account via Option A step 2 first, then:
bw login <email-you-registered>
export BW_SESSION=$(bw unlock --raw)
bw import bitwardenjson vaultwarden-import.json
```

> **After importing**, delete the plaintext file:
>
> ```bash
> rm vaultwarden-import.json
> ```
>
> It's gitignored (won't leak via git) but anything running as your user can still read it from disk.

---

## 8. Single sign-on (Keycloak)

Bootstrap creates a `gitops` realm in Keycloak, wires OIDC clients for both Gitea and ArgoCD, and seeds a test user.

#### Test user

| Realm | Username | Password |
|---|---|---|
| `gitops` | `dev` | `dev` |

#### Login flow

- On **ArgoCD**: click *LOG IN VIA KEYCLOAK*.
- On **Gitea**: click *Sign in with Keycloak*.
- On **Keycloak account console**: [http://keycloak.local/realms/gitops/account](http://keycloak.local/realms/gitops/account).

#### Adding users to the realm

1. Log into Keycloak as `admin` (see `.env`).
2. Switch to the **gitops** realm (top-left dropdown).
3. **Users → Add user**. Fill in username/email, click **Create**.
4. **Credentials** tab → **Set password**. Toggle *Temporary* off if you want it permanent.

Users can also self-register — registration is enabled on the `gitops` realm.

---

## 9. Deploy your own application

The full recipe lives in [`docs/ADD_YOUR_APP.md`](ADD_YOUR_APP.md). Short version:

1. **Copy the template** from `gitops-repo/apps-examples/my-app.yaml.example` to `gitops-repo/apps/my-app.yaml` and edit the name/URL/path.
2. **Drop your source into** `app-repos/my-app/` (Dockerfile + `manifests/` + optional `build.sh`) — bootstrap phase 07b mirrors it to Gitea automatically.
3. **Commit + push** the gitops-repo. Root app-of-apps materialises the Application within seconds.
4. **Build + import the image** via `app-repos/my-app/build.sh` (runs in phase 09c). Example content:

    ```bash
    #!/usr/bin/env bash
    set -euo pipefail
    HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ARCH="$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')"
    IMAGE="my-app:dev"
    TARBALL="/tmp/my-app.tar"
    docker build --platform "linux/$ARCH" -t "$IMAGE" "$HERE"
    docker save "$IMAGE" -o "$TARBALL"
    for n in k3d-gitops-local-server-0 k3d-gitops-local-agent-0 k3d-gitops-local-agent-1; do
      docker cp "$TARBALL" "$n:/tmp/img.tar"
      docker exec "$n" ctr -n k8s.io images import /tmp/img.tar
      docker exec "$n" rm /tmp/img.tar
    done
    rm -f "$TARBALL"
    ```

5. **Add a hostname** to `/etc/hosts`: `127.0.0.1 my-app.local`. Patch CoreDNS if pods need to reach it.

> **Claude Code shortcut:** The `/docker-build-import` skill automates the tarball import loop.

---

## 10. Headscale VPN (mobile demos)

Free, self-hosted Tailscale control plane deployed as an ArgoCD Application (`platform/headscale`). Point the official Tailscale mobile apps at [http://headscale.local](http://headscale.local) to mesh your phone with the landing zone.

### Why Headscale

- **100% free, no paid tier** — BSD-3-licensed open-source implementation of the Tailscale coordination server. No Tailscale account required, no traffic routed through Tailscale's SaaS.
- **Uses the official mobile apps** — Tailscale iOS / Android from the stores; just point them at your self-hosted control URL.
- **WireGuard under the hood** — NAT traversal via an embedded DERP relay; Magic DNS for hostname resolution; subnet routing so the phone can reach `portal.local` etc.

### First-run setup

```bash
# 1. Create a user
kubectl -n headscale exec deploy/headscale -- headscale users create alice

# 2. Generate a pre-auth key (reusable, 7-day expiry)
kubectl -n headscale exec deploy/headscale -- headscale preauthkeys create \
  --user alice --reusable --expiry 168h
# → copy the printed key
```

Or use the convenience wrappers in the `platform/headscale` repo:

```bash
bash scripts/create-user.sh alice
bash scripts/create-preauth.sh alice --reusable --expiry 168h
```

### Registering a phone

1. Install the **Tailscale** app (App Store / Play Store).
2. Configure the custom control server:
    - **iOS**: Settings → Use alternate server → `http://headscale.local`.
    - **Android**: Settings → Change server → `http://headscale.local`.
3. Tap **Sign in with auth key** and paste the pre-auth key.

The phone now has a `100.x.x.x` Tailscale IP and a `*.landingzone.local` Magic DNS name.

### Reaching the landing zone from the phone

The phone can see other Tailscale nodes, but to reach `portal.local` / `gitea.local:3000` / etc. — which live on the cluster's `172.20.0.0/24` — register the laptop as a Tailscale **subnet router**:

```bash
# On the landing-zone host:
brew install tailscale        # or download from https://tailscale.com/download
sudo tailscale up \
  --login-server http://headscale.local \
  --auth-key <another-preauth-key> \
  --advertise-routes 172.20.0.0/24

# On Headscale, approve the route:
kubectl -n headscale exec deploy/headscale -- headscale routes list
kubectl -n headscale exec deploy/headscale -- headscale routes enable --route <id-from-list>
```

### Phone on cellular / different network

The landing zone's `headscale.local` is only reachable on the same LAN as the laptop. For demos where the phone is on 4G / 5G or a different WiFi, either:

1. **Cloudflare Tunnel** (recommended, still free): expose Headscale at a public HTTPS URL via `cloudflared`; update `server_url` in the ConfigMap accordingly. No paid Cloudflare tier needed for dev.
2. Move Headscale to a cheap VPS and keep this instance as dev-only.

### Admin commands

```bash
kubectl -n headscale exec deploy/headscale -- headscale --help

# Common:
kubectl -n headscale exec deploy/headscale -- headscale users list
kubectl -n headscale exec deploy/headscale -- headscale nodes list
kubectl -n headscale exec deploy/headscale -- headscale routes list
```

---

## 11. Lifecycle

### 11.1 Resume after stop

Most reliable way to bring the stack back up after `colima stop` or `./teardown.sh` (volumes preserved):

```bash
./scripts/resume.sh
```

This starts Colima + compose + k3d, re-applies the transient fixes (Colima daemon DNS, k3d node `/etc/resolv.conf`, CoreDNS NodeHosts for `gitea.local`), bounces dnsmasq + CoreDNS, and clears any ghost Pending / Terminating / CrashLoopBackOff pods from the pre-stop state. Idempotent — safe to re-run.

### 11.2 Restart (manual)

If you prefer the manual sequence — or are debugging the resume flow:

**Stop:**

```bash
k3d cluster stop gitops-local
docker compose -f docker-compose/docker-compose.yml stop
# Optional: colima stop
```

**Start:**

```bash
# macOS only: after a machine reboot, start Colima first
colima start

docker compose -f docker-compose/docker-compose.yml start
k3d cluster start gitops-local

# Re-patch CoreDNS NodeHosts for gitea.local (wiped on every cluster restart)
GITEA_IP=$(docker inspect gitea --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
kubectl -n kube-system get cm coredns -o json | \
  sed "s|\"NodeHosts\": \"|\"NodeHosts\": \"$GITEA_IP gitea.local\\n|" | \
  kubectl apply -f -
kubectl -n kube-system rollout restart deploy coredns
```

### 11.3 Teardown

```bash
./teardown.sh       # Linux / WSL / macOS
# — or —
.\teardown.ps1      # Windows (elevated PowerShell)
```

Deletes the k3d cluster and brings the compose stack down. **Volumes are preserved** unless you also run `docker volume prune`. Re-running `./bootstrap.sh` after a teardown is the supported upgrade path.

### 11.4 Backup & restore

```bash
./scripts/backup.sh                             # → backups/backup-YYYY-MM-DD-HHMMSS.tar.gz
./scripts/restore.sh backups/backup-<date>.tar.gz
```

Backs up: Gitea PostgreSQL dump, Keycloak realm JSON exports, Vaultwarden SQLite, and `.env`. Extend the *CUSTOM* section at the bottom of either script to include your own application databases.

---

## 12. Observability

The `monitoring` ArgoCD application deploys the `kube-prometheus-stack` + `loki` + `promtail` Helm charts.

#### Grafana

- URL: `http://grafana.local` · `admin` / see `.env`
- Pre-loaded dashboard: **Landing Zone Overview** (pod counts, node CPU/memory, restart rates, namespace breakdown).
- Add more dashboards as ConfigMaps with label `grafana_dashboard: "1"` in the `monitoring` namespace; the sidecar picks them up automatically.

#### Prometheus

- URL: `http://prometheus.local`
- Retention: 7 days. Edit `gitops-repo/apps/monitoring.yaml` to change.
- 4 alert groups in `gitops-repo/manifests/monitoring/alerts.yaml`: pod-health, node-health, argocd, service-health.
- Alertmanager is disabled by default — enable and point at your notification channel in `apps/monitoring.yaml`.

#### Loki + Promtail

Promtail runs as a DaemonSet on every k3d node and forwards pod logs to Loki. Query them in Grafana's **Explore** tab with the *Loki* data source. LogQL example:

```
{namespace="keycloak"} |= "error"
```

---

## 13. Security

- **No secrets in Git**. `.env` is gitignored; Kubernetes secrets use Sealed Secrets; `gitops-repo/manifests/**` is templated with `${VAR}` placeholders and rendered into a temp dir on push — the source tree never holds resolved secrets.
- **Pre-commit hooks**. `scripts/pre-commit-hook.sh` runs a gitleaks-style secret scan + YAML validation + Helm lint on every commit. Install them in every local repo with `bash scripts/install-hooks.sh`.
- **NetworkPolicies**. Default-deny-ingress in every namespace with explicit allowlists for Traefik / OIDC / Prometheus / intra-namespace traffic.
- **Vaultwarden**. Self-hosted password manager with HTTPS (self-signed). Admin panel requires the token from `.env`.
- **Keycloak OIDC**. SSO for Gitea + ArgoCD. A `dev / dev` test user is seeded in the `gitops` realm — remove it before treating this instance as anything production-adjacent.

---

## 14. AI agent federation

The landing zone ships with six Claude Code agents and 12 reusable skills. See the [federation page](http://portal.local/federation.html) for diagram + full agent list.

| Agent | Depth | Owns |
|---|---|---|
| orchestrator | 0 | Top-level dispatch, cross-domain resolution |
| platform-infra | 1 | k3d, Docker, MetalLB, Gitea, ArgoCD, Keycloak |
| cluster-lifecycle | 2 | k3d + MetalLB + CoreDNS + image imports |
| gitea-argocd | 2 | Gitea + ArgoCD + Keycloak OIDC |
| observability | 1 | Prometheus, Grafana, Loki, dashboards, alerts |
| security | 1 | Sealed Secrets, Vaultwarden, RBAC, NetworkPolicies |

#### Slash commands (skills)

`/kubectl-status` · `/argocd-sync` · `/docker-build-import` · `/helm-validate` · `/kubeseal-secret` · `/bootstrap-phase` · `/gitea-api` · `/keycloak-admin` · `/kustomize-build` · `/grafana-dashboard` · `/cluster-health` · `/netpol-test`

---

## 15. Platform-specific notes

### 15.1 macOS / Colima

- **Images build native, no Rosetta required.** `scripts/09b-build-portal.sh` and `app-repos/*/build.sh` auto-detect host arch and build `linux/arm64` on Apple Silicon, `linux/amd64` on Intel. Multi-arch images are avoided because `ctr import` on k3d nodes only handles single-arch manifests.
- **DNS patch.** Colima's internal `192.168.5.2` resolver is not reachable from the nested `gitops` Docker bridge, so `04-create-k3d-cluster.sh` rewrites each node's `/etc/resolv.conf` to `8.8.8.8`/`1.1.1.1`. Docker marks that file user-editable, so the patch persists.
- **Daemon-level DNS pin.** `00-prerequisites.sh` writes `/etc/docker/daemon.json` with `{"dns":["8.8.8.8","1.1.1.1"]}` so newly-created containers (including k3d nodes after a restart) inherit public DNS automatically.
- **inotify tuning.** `00-prerequisites.sh` writes `/etc/sysctl.d/99-k3d-inotify.conf` inside the Colima VM. Without it Promtail crashloops with `too many open files`.
- **`.local` mDNS quirk.** macOS treats `.local` as multicast DNS. `/etc/hosts` entries work, but the first cold lookup per hostname may take ~5 s before falling back. Subsequent requests are instant. Use `curl --resolve portal.local:80:127.0.0.1` to bypass entirely.
- **dnsmasq :53 not bound on the host.** The `docker-compose.linux.yml` override strips it; in-cluster containers still reach dnsmasq at `172.20.0.2:53`.
- **Docker Compose v2 plugin.** `brew install docker-compose` ships only the standalone binary — the bootstrap symlinks it into `~/.docker/cli-plugins/` so the subcommand form works.

### 15.2 Linux / WSL2

- **systemd-resolved vs. dnsmasq.** The dnsmasq `:53` host binding conflicts with systemd-resolved, so the `docker-compose.linux.yml` override drops the port mapping. Same outcome as macOS.
- **WSL2** is detected as `wsl` by the platform code but treated the same as native Linux. Enable Docker Desktop's WSL2 integration or run Docker Engine directly inside the distro.

### 15.3 Windows

- **Hosts file requires Admin.** `bootstrap.ps1` relaunches itself with elevation for the first run.
- **MSYS path translation.** When Git Bash passes a Unix path to `docker exec`, MSYS rewrites it unless you set `MSYS_NO_PATHCONV=1`. The `/docker-build-import` skill documents this.

---

## 16. Troubleshooting

### All `*.local` URLs return `000` or time out

- Confirm `/etc/hosts` has the entries: `grep gitops-local-dev /etc/hosts`.
- On macOS, cold lookups on `.local` can wait ~5 s on mDNS — curl with `--resolve` bypasses it:

    ```bash
    curl --resolve portal.local:80:127.0.0.1 http://portal.local/
    ```

- Check Traefik is running and port 80/443 is bound: `netstat -an | grep LISTEN | grep '\.80 '`.

### An ArgoCD Application is stuck OutOfSync

```bash
# Flush the repo-server cache (ArgoCD keeps rendered Helm output ~3 min)
kubectl -n argocd rollout restart deploy argocd-repo-server

# Then force a hard refresh on the Application
kubectl annotate application <name> -n argocd \
  argocd.argoproj.io/refresh=hard --overwrite
```

### Promtail pods CrashLoopBackOff with *too many open files*

The Colima VM's inotify limits are too low. Bootstrap tunes them automatically; if you're seeing this on an existing cluster:

```bash
colima ssh -- sudo sh -c '
cat > /etc/sysctl.d/99-k3d-inotify.conf <<EOF
fs.inotify.max_user_watches   = 524288
fs.inotify.max_user_instances = 512
fs.file-max                   = 524288
EOF
sysctl -p /etc/sysctl.d/99-k3d-inotify.conf'
kubectl -n monitoring rollout restart ds promtail
```

### Promtail pods `0/1` Ready but process is running fine

The readiness probe depends on kubernetes service-discovery returning ≥1 target. After a stop/start cycle, the SD loop may miss that window on some nodes. If logs are flowing to Loki anyway, this is cosmetic. To clear it: delete the monitoring Application and let root app-of-apps re-sync it.

### Keycloak pod stuck in startup for 5+ minutes

Quarkus's first-run AOT compile is slow on arm64 — expected, not a failure. Watch progress:

```bash
kubectl logs -n keycloak keycloak-keycloakx-0 --follow
```

### A pod on one specific k3d node keeps restarting with *connection refused* to 10.43.0.1:443

Kube-proxy on that node has stale iptables rules (usually after a Docker engine restart or manually restarting the k3d container). Symptoms in logs:

```
failed to list *v1.Pod: ... dial tcp 10.43.0.1:443: connect: connection refused
```

Fix — restart the affected node's k3d container (non-destructive; pods reschedule):

```bash
docker restart k3d-gitops-local-agent-1
# then re-patch its DNS (Docker wipes /etc/resolv.conf on restart)
docker exec k3d-gitops-local-agent-1 sh -c \
  'printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions ndots:0\n" > /etc/resolv.conf'
# delete any pods that were crashlooping — they'll reschedule cleanly
kubectl delete pod -A --field-selector=status.phase!=Running,status.phase!=Succeeded
```

Starting from v1.5.1, the bootstrap pins Docker daemon DNS inside the Colima VM so new containers (including k3d nodes after a restart) inherit `8.8.8.8`/`1.1.1.1` automatically — you no longer need to re-patch by hand.

### Grafana login fails with *Invalid username or password*

- Confirm the live Secret matches `.env`:

    ```bash
    diff <(grep ^GRAFANA_ADMIN_PASSWORD .env | cut -d= -f2-) \
         <(kubectl -n monitoring get secret monitoring-grafana \
            -o jsonpath='{.data.admin-password}' | base64 -d)
    ```

- If they diverge, flush ArgoCD's repo-server cache (see above), re-sync `monitoring`, then restart the Grafana Deployment to re-read its env vars.

### Bootstrap fails with *Keycloak realm 'gitops' not ready*

The PostSync Job that creates the realm auto-deletes on success. If you need to re-run it manually after bootstrap finishes:

```bash
kubectl -n keycloak delete job keycloak-configure --ignore-not-found
bash scripts/10-configure-oidc.sh
```

### Need to rotate all secrets

```bash
# Back up first if you care about the data
./scripts/backup.sh

# Wipe + regenerate
./teardown.sh
./scripts/gen-env.sh --force
./bootstrap.sh
```

---

## 17. Extending further

- **Add a domain agent**: drop a file in `.claude/agents/my-domain.md` following the pattern in `platform-infra.md`. Claude Code picks it up on next session.
- **Add a Grafana dashboard**: `kubectl create configmap my-dashboard --from-file=my-dashboard.json -n monitoring` + label `grafana_dashboard: "1"`.
- **Webhook-based instant sync**: `bash scripts/configure-webhooks.sh` adds a Gitea webhook for each repo pointing at ArgoCD's `/api/webhook` — push triggers sync within seconds instead of 3-min polling.
- **Pre-commit hooks in other repos**: `bash scripts/install-hooks.sh` installs the secret/YAML/helm lint in every local repo under the same parent directory.

### Further reading

- [`README.md`](../README.md) — project overview and quick-start (GitHub-rendered).
- [`CLAUDE.md`](../CLAUDE.md) — agent federation design.
- [`REPRODUCTION.md`](../REPRODUCTION.md) — detailed bootstrap walkthrough.
- [`docs/ADD_YOUR_APP.md`](ADD_YOUR_APP.md) — deploy a new workload on top of the landing zone.
- [`CHANGELOG.md`](../CHANGELOG.md) — release history.
