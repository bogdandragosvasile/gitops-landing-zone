# GitOps Landing Zone

A fully reproducible local GitOps platform. Clone, run `./bootstrap.sh`, wait ~15–25 minutes, and you have a self-hosted Git server, an ArgoCD controller, SSO, observability, and Kubernetes — all on your laptop. Then deploy your own applications on top via ArgoCD.

![status](https://img.shields.io/badge/status-ready--to--fork-green)
![linux](https://img.shields.io/badge/Linux%20%7C%20WSL2-supported-success)
![macos](https://img.shields.io/badge/macOS%20(Intel%20%2F%20Apple%20Silicon)-supported-success)
![windows](https://img.shields.io/badge/Windows%2011-supported-success)
![stack](https://img.shields.io/badge/stack-k3d%20%2B%20ArgoCD%20%2B%20Gitea%20%2B%20Keycloak-red)

---

## What you get

| Service | Purpose |
|---|---|
| **k3d** | 3-node Kubernetes cluster running inside your Docker engine |
| **Gitea** | Self-hosted Git server + container registry + Actions CI runner |
| **ArgoCD** | GitOps controller with app-of-apps pattern |
| **Keycloak** | SSO / OIDC for Gitea + ArgoCD |
| **cert-manager** | TLS certificate automation |
| **Sealed Secrets** | Encrypt Kubernetes secrets for Git storage |
| **MetalLB** | LoadBalancer for the local Docker network |
| **Prometheus + Grafana + Loki** | Full observability stack with dashboards |
| **Vaultwarden** | Bitwarden-compatible password manager |
| **dnsmasq** | In-cluster DNS resolver for `*.local` domains |
| **Landing Portal** | Unified entry point at `portal.local` with dark/light theme |
| **AI Agent Federation** | 6 Claude Code agents + 12 skills for infrastructure management |

All traffic routed through Traefik ingress with `*.local` hostnames.

## Architecture

```
Host (Linux / WSL2 / macOS — Colima or Docker Desktop / Windows 11 — Docker Desktop)
│
├── Docker Network: gitops (172.20.0.0/24)
│   ├── gitea + gitea-db + gitea-runner
│   ├── vaultwarden (HTTPS 8443)
│   └── dnsmasq (*.local → 127.0.0.1)
│
└── k3d cluster (1 server + 2 agents)
    ├── Traefik ingress (ports 80/443)
    ├── MetalLB (172.20.0.100-150)
    ├── ArgoCD (app-of-apps — 10 child Applications)
    ├── Keycloak + PostgreSQL
    ├── cert-manager, sealed-secrets
    ├── Prometheus + Grafana + Loki + Promtail
    └── portal.local (landing page)
```

## Prerequisites

### Linux / WSL2

| Component | Notes |
|---|---|
| Docker Engine | 20.x+ with ≥16 GB memory available — `sudo systemctl start docker` |
| `bash`, `git`, `curl`, `openssl`, `envsubst` (`gettext-base`), `python3` | |
| `sudo` | needed for `/etc/hosts` on first run |
| ~30 GB free disk |  |

### macOS (Intel or Apple Silicon)

| Component | Notes |
|---|---|
| [Homebrew](https://brew.sh) | required |
| [Colima](https://github.com/abiosoft/colima) | `brew install colima docker docker-compose` |
| `brew install git curl openssl gettext python@3.12` | `gettext` provides `envsubst`, `grep` (optional — `ggrep` enables `-P` regex in the pre-commit hook) |
| `sudo` | needed for `/etc/hosts` on first run |
| ~30 GB free disk |  |

Start the Colima VM with enough headroom for the cluster **and** your host (macOS still needs memory to breathe):

```bash
colima start --cpu 6 --memory 10 --disk 60
```

For 16 GB machines, **don't** give Colima more than ~10 GB — the stack fits, and leaving 6 GB to macOS prevents swap thrashing during long-running operations (Keycloak boot, Helm installs, Docker builds).

Optional Rosetta (if you need to run x86_64 images occasionally):

```bash
colima start --cpu 6 --memory 10 --disk 60 --vm-type vz --vz-rosetta
```

The stack itself runs natively on arm64 — scripts auto-detect host arch and build single-arch `linux/arm64` or `linux/amd64` images accordingly.

### Windows 11

| Component | Notes |
|---|---|
| Docker Desktop | ≥16 GB RAM allocated to WSL2 |
| Git for Windows (provides Git Bash) | |
| PowerShell | elevated, for `bootstrap.ps1` and first-run hosts-file write |
| ~30 GB free disk | |

## Quick start

### Linux / WSL2 / macOS — zero manual edits

```bash
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone

# macOS only: make sure Colima is up
colima start --cpu 6 --memory 10 --disk 60

./bootstrap.sh
```

`bootstrap.sh` automatically:

1. Generates `.env` with URL-safe random secrets (`scripts/gen-env.sh`).
2. Writes `*.local` entries to `/etc/hosts` (requests sudo — on macOS without a TTY, it opens the native admin GUI prompt).
3. Installs `k3d`, `helm`, `kubectl`, `argocd`, `kubeseal`, `envsubst`, and wires the `docker compose` CLI plugin (brew-first on macOS, binary fallback on Linux).
4. Raises `fs.inotify.max_user_watches` inside the Colima VM on macOS (promtail otherwise crashloops on `too many open files`).
5. Creates the shared `gitops` Docker network, starts the compose stack (gitea, postgres, vaultwarden, dnsmasq), provisions Gitea admin/org/runner.
6. Creates the 3-node k3d cluster, patches DNS (Colima's internal resolver is unreachable from the nested bridge), waits for `kube-system`.
7. Installs MetalLB + ArgoCD via Helm, sets the ArgoCD admin password by patching `argocd-secret` with a bcrypt hash (no CLI login required).
8. Renders `gitops-repo/` into a temp dir (never mutates the source tree), pushes to Gitea, registers it in ArgoCD, applies the root Application.
9. Builds + imports the landing portal image into all k3d nodes (`ctr -n k8s.io images import`).
10. Runs the Keycloak realm/client/user bootstrap Job, then wires the Gitea OIDC authentication source.

Runtime on first run: ~15–25 min. Re-runs are idempotent.

### Windows 11

```powershell
git clone https://github.com/bogdandragosvasile/gitops-landing-zone.git
cd gitops-landing-zone
cp .env.example .env
# Edit .env — replace each CHANGE_ME_* with a strong secret

# From an ELEVATED PowerShell:
.\bootstrap.ps1
```

The PowerShell wrapper finds Git Bash and hands off to `scripts/bootstrap.sh`.

### Generate your own `.env`

`bootstrap.sh` only creates `.env` if it's missing. To regenerate it on demand (e.g. after a password leaked):

```bash
./scripts/gen-env.sh --force     # backs up the current .env with a timestamp
```

All passwords it emits are alphanumeric — the platform Keycloak admin-cli token flow decodes `+` as space in form-urlencoded bodies, so the helper strips them.

## What's in the box

```
gitops-landing-zone/
├── bootstrap.sh / teardown.sh       # Unix entry points (Linux / WSL / macOS)
├── bootstrap.ps1 / teardown.ps1     # Windows entry points (elevated PowerShell)
├── .env.example                     # Template — gen-env.sh renders this into .env
├── docker-compose/
│   ├── docker-compose.yml           # gitea + gitea-db + gitea-runner + vaultwarden + dnsmasq
│   ├── docker-compose.linux.yml     # Unix override: drops dnsmasq :53 host bind
│   └── certs/                       # gitignored — scripts/01b-ensure-certs.sh creates vault.{crt,key}
├── k3d/
│   └── k3d-config.yaml              # 1 server + 2 agents, ports 80/443 on loadbalancer
├── scripts/
│   ├── bootstrap.sh                 # Master orchestrator
│   ├── teardown.sh                  # Full cluster + compose cleanup (volumes preserved)
│   ├── gen-env.sh                   # Auto-render .env with URL-safe random secrets
│   ├── setup-hosts.sh               # /etc/hosts entries (osascript GUI fallback on macOS)
│   ├── 00-prerequisites.sh          # brew-aware installer, validates .env, tunes Colima sysctls
│   ├── 01-create-network.sh         # Docker network
│   ├── 01b-ensure-certs.sh          # Vaultwarden self-signed TLS cert
│   ├── 02-start-gitea.sh            # Bring up the compose stack
│   ├── 03-configure-gitea.sh        # Admin user + org + runner registration
│   ├── 04-create-k3d-cluster.sh     # Cluster + DNS patch + Traefik hostPort + CoreDNS NodeHosts
│   ├── 05-install-metallb.sh        # LoadBalancer + IPAddressPool
│   ├── 06-install-argocd.sh         # ArgoCD Helm install + bcrypt admin password patch
│   ├── 07-push-gitops-repo.sh       # Render gitops-repo to tempdir + push to Gitea
│   ├── 08-configure-argocd-repo.sh  # Register Gitea repo secret
│   ├── 09-apply-app-of-apps.sh      # Root Application + wait for children
│   ├── 09b-build-portal.sh          # Build + ctr-import landing portal image
│   ├── 10-configure-oidc.sh         # Run Keycloak bootstrap Job + Gitea OIDC source
│   ├── configure-dns.sh             # Optional: Windows NRPT rule for *.local
│   ├── configure-webhooks.sh        # Gitea → ArgoCD push webhooks (instant sync)
│   ├── backup.sh / restore.sh       # pg_dump + Keycloak realms + Vaultwarden archive
│   ├── install-hooks.sh             # Install pre-commit hooks in every local repo
│   ├── pre-commit-hook.sh           # Secret scan + YAML lint + helm lint
│   └── lib/common.sh                # Platform/arch detection, logging, password helpers
├── gitops-repo/                     # Template — pushed as platform/gitops-infra on first bootstrap
│   ├── apps/                        # 10 ArgoCD Applications (the base platform)
│   ├── apps-examples/               # my-app.yaml.example — copy-paste template
│   ├── manifests/                   # Raw K8s manifests + helm values
│   └── .gitea/workflows/
│       └── validate.yaml            # YAML + helm lint pipeline
├── docs/
│   └── ADD_YOUR_APP.md              # How to deploy your own application
└── .claude/                         # AI agent federation
    ├── agents/                      # 6 agent definitions
    ├── skills/                      # 12 reusable slash commands
    └── settings.json                # Permissions + audit hooks
```

## Extending with your own applications

See [`docs/ADD_YOUR_APP.md`](docs/ADD_YOUR_APP.md) for the full recipe. In short:

1. Copy the template `gitops-repo/apps-examples/my-app.yaml.example` to `gitops-repo/apps/my-app.yaml` and edit the name/URL/path.
2. Create the matching repo (`platform/my-app`) in the local Gitea and push your Kubernetes manifests.
3. `git push` the gitops-repo — root app-of-apps materializes your Application within seconds.
4. Build + import the image (`docker build --platform linux/<arch> ... | docker save | ctr -n k8s.io images import`) — see `.claude/skills/docker-build-import/SKILL.md`.
5. Add `127.0.0.1 my-app.local` to `/etc/hosts` and, if needed, patch CoreDNS NodeHosts.

For a larger app with its own agent, drop a file into `.claude/agents/my-app.md` following the pattern in `.claude/agents/platform-infra.md`.

## Example apps (separate repo)

The BankOffer AI + CareerForge workloads that originally validated this platform have been moved to the sibling repo [`my-testing-apps`](https://github.com/bogdandragosvasile/my-testing-apps). Use that as a reference for a non-trivial multi-service deployment on top of the landing zone; this repo stays a clean base platform.

## Service URLs

After bootstrap, these resolve to `127.0.0.1` via `/etc/hosts`:

| URL | Service | Credentials |
|---|---|---|
| http://portal.local | Landing portal (overview of everything) | — |
| http://argocd.local | ArgoCD UI | `admin` / see `.env` (`ARGOCD_ADMIN_PASSWORD`) |
| http://gitea.local:3000 | Gitea web + API | `gitea_admin` / see `.env` (`GITEA_ADMIN_PASSWORD`) |
| http://keycloak.local | Keycloak admin | `admin` / see `.env` (`KEYCLOAK_ADMIN_PASSWORD`) |
| http://grafana.local | Grafana dashboards | `admin` / see `.env` (`GRAFANA_ADMIN_PASSWORD`) |
| http://prometheus.local | Prometheus UI | — |
| https://localhost:8443 | Vaultwarden | your master password (admin token in `.env`) |

For SSO flows, log in via Keycloak in the `gitops` realm as **dev / dev**.

## Security

- **No secrets in Git** — `.env` is gitignored; Kubernetes secrets use Sealed Secrets; `gitops-repo/manifests/**` is templated with `${VAR}` placeholders and rendered into a temp dir on push (the source tree never holds resolved secrets).
- **Pre-commit hooks** — `scripts/pre-commit-hook.sh` runs gitleaks-style secret pattern scan + YAML validation + Helm lint; installs via `scripts/install-hooks.sh`. macOS BSD grep doesn't support Perl regex — `brew install grep` provides `ggrep` which the hook auto-detects.
- **Network policies** — default-deny-ingress in every namespace with explicit Traefik/OIDC/postgres/prometheus allowlists.
- **Vaultwarden** — self-hosted credentials vault (HTTPS with locally-generated self-signed cert).
- **Keycloak OIDC** — SSO for Gitea and ArgoCD; a `dev/dev` test user is seeded in the `gitops` realm.

## Operational scripts

| Script | Purpose |
|---|---|
| `scripts/backup.sh` | Backup Gitea DB + Keycloak realms + Vaultwarden + `.env` → `backups/<timestamp>.tar.gz` |
| `scripts/restore.sh <archive>` | Restore from a backup |
| `scripts/configure-dns.sh` | Set up local DNS (NRPT on Windows, validate `/etc/hosts` elsewhere) |
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

Deletes the k3d cluster and brings the compose stack down. Volumes are preserved unless you also `docker volume prune`.

## Graceful restart

**Stop:**

```bash
k3d cluster stop gitops-local
docker compose -f docker-compose/docker-compose.yml stop
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

## Platform-specific notes

### macOS / Colima

- **Images build native — no Rosetta required.** `scripts/09b-build-portal.sh` auto-detects host arch via `common.sh` (`PLATFORM_ARCH`) and passes `--platform linux/arm64` on Apple Silicon, `linux/amd64` on x86_64. Multi-arch images are avoided because `ctr import` on k3d nodes only handles single-arch manifests.
- **DNS patch.** Colima's internal `192.168.5.2` resolver is not reachable from the nested `gitops` Docker network, so `scripts/04-create-k3d-cluster.sh` rewrites each k3d node's `/etc/resolv.conf` to `8.8.8.8`/`1.1.1.1` right after creation. Docker marks that file user-editable, so the patch persists for the lifetime of the node.
- **inotify tuning.** `scripts/00-prerequisites.sh` writes `/etc/sysctl.d/99-k3d-inotify.conf` inside the Colima VM (`fs.inotify.max_user_watches=524288`, `max_user_instances=512`, `file-max=524288`) and runs `sysctl -p`. Without it Promtail crashloops with `too many open files`.
- **`.local` mDNS quirk.** macOS treats `.local` as multicast DNS (Bonjour). The `/etc/hosts` entries work, but the resolver may take 5 s on a cold lookup before falling back to hosts. This doesn't affect the cluster — Traefik hostPort binds 80/443 on localhost directly — it only means the first request to `portal.local` after a fresh boot may be slow. `curl --resolve portal.local:80:127.0.0.1 http://portal.local/` bypasses it entirely.
- **dnsmasq port 53 not bound on the host.** The `docker-compose.linux.yml` override strips the `:53` host binding on all unix platforms; in-cluster containers still hit dnsmasq at `172.20.0.2:53`. Host-side resolution uses `/etc/hosts` exclusively.
- **`docker compose` v2 plugin.** `brew install docker-compose` ships only the standalone `docker-compose` binary. `scripts/00-prerequisites.sh` symlinks it into `~/.docker/cli-plugins/docker-compose` so scripts can use the subcommand form.
- **GNU grep recommended.** `brew install grep` installs `ggrep` with Perl-regex support (`-P`); the pre-commit hook auto-detects it and falls back to limited matching if unavailable.

### Linux / WSL2

- **systemd-resolved vs dnsmasq.** dnsmasq's `:53` host binding conflicts with systemd-resolved on Ubuntu/Debian, so the `docker-compose.linux.yml` override drops the port mapping. Same outcome as on macOS — `/etc/hosts` handles host-side resolution, dnsmasq handles in-cluster only.
- **WSL2.** Treated the same as native Linux by the platform detector. Make sure Docker Desktop's WSL2 integration is enabled or run Docker Engine directly inside your WSL2 distro.

### Windows 11

- **Hosts file requires Admin.** `bootstrap.ps1` relaunches itself with elevation to edit `C:\Windows\System32\drivers\etc\hosts` on the first run.
- **`MSYS_NO_PATHCONV=1` for `docker exec`.** When Git Bash passes a Unix path to `docker exec`, MSYS rewrites it unless you set `MSYS_NO_PATHCONV=1`. The `/docker-build-import` skill documents this; pure Unix hosts don't need it.

## Tested platforms

| Host | Status | Notes |
|---|---|---|
| **macOS 14 + Colima** (M1 Pro, 16 GB) | ✅ End-to-end verified | Allocate 6 CPU / 10 GB to Colima to leave macOS headroom |
| **Ubuntu 22.04 + Docker Engine** | ✅ Supported (same scripts) | ≥16 GB host RAM recommended |
| **WSL2 + Docker Desktop** | ✅ Supported (same scripts) | Enable WSL2 integration in Docker Desktop |
| **Windows 11 + Docker Desktop** | ✅ Supported (`bootstrap.ps1`) | Elevate once for hosts file |

## Contributing

This is a personal model / template. Fork it, rip out what you don't need, add your own applications via the pattern in `docs/ADD_YOUR_APP.md`.

## License

MIT
