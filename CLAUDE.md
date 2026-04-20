# GitOps Landing Zone — Agent Federation

## What is this

A fully reproducible local GitOps development environment.
Supported hosts: Windows 11 + Docker Desktop, Linux/WSL + Docker Engine, macOS + Colima (Apple Silicon ok).
Base platform only — deploy your own applications via ArgoCD once the zone is up.

Managed by a 6-agent federation with depth-2 hierarchy, coordinated via `.claude/team-state.json`.

## Architecture

```
orchestrator (depth 0)
├── platform-infra (depth 1)
│   ├── cluster-lifecycle (depth 2) — k3d, network, metallb, coredns
│   └── gitea-argocd (depth 2) — gitea, argocd, keycloak, oidc
├── observability (depth 1) — prometheus, grafana, loki
└── security (depth 1) — sealed-secrets, vaultwarden, rbac, netpol
```

## Agent Protocol

1. Every agent reads `.claude/team-state.json` before starting work.
2. Every agent updates its status and appends to the history array after each task.
3. Agents NEVER modify files outside their owned paths.
4. Cross-domain dependencies are tracked via readiness flags in team-state.json.

## Execution Phases

```
Phase 1 (sequential): cluster-lifecycle → gitea-argocd
Phase 2 (sequential): security
Phase 3 (sequential): observability
Phase 4 (verification): cluster-health
```

## Skills (slash commands)

| Skill | Purpose |
|---|---|
| `/kubectl-status` | Namespace health summary |
| `/argocd-sync` | Sync app + wait for healthy |
| `/docker-build-import` | Build image + k3d import |
| `/helm-validate` | Lint + template validate |
| `/kubeseal-secret` | Seal secret with kubeseal |
| `/bootstrap-phase` | Run numbered bootstrap script |
| `/gitea-api` | Gitea REST API wrapper |
| `/keycloak-admin` | Keycloak Admin API wrapper |
| `/kustomize-build` | Build + validate kustomize overlay |
| `/grafana-dashboard` | Upload Grafana dashboard |
| `/cluster-health` | Full cluster verification |
| `/netpol-test` | Network connectivity test |

## Extending

Add your own applications as ArgoCD Applications. For each domain you add:
1. Create a new agent at `.claude/agents/<your-domain>.md` following the pattern in `platform-infra.md`
2. Create matching manifests in a new repo (e.g., `myapp-platform`) pushed to Gitea
3. Add an ArgoCD Application manifest in `gitops-repo/apps/myapp.yaml`
4. Root app-of-apps picks it up automatically

## Key Invariants

- **CoreDNS NodeHosts wiped on every k3d restart** — re-patch required
- **k3d image import fails on multi-arch** — use `docker save | ctr import` tarball method; builds pass single-arch `--platform linux/${PLATFORM_ARCH}` (auto-detected in `common.sh`)
- **`imagePullPolicy: Always` breaks offline** — use `Never` for locally imported images
- **ArgoCD operationState caches old revisions** — restart repo-server to flush
- **Bitnami Docker Hub tags removed** — mirror to local Gitea registry
- **dnsmasq :53 host bind is dropped on unix** (Linux/WSL/macOS) via `docker-compose.linux.yml` override — the host uses `/etc/hosts`, in-cluster uses `172.20.0.2:53`
- **macOS requires Colima running** — `colima start` before `./bootstrap.sh`

## Credentials

All in `.env` (gitignored). Copy `.env.example` to `.env` and fill in.

## Startup / Shutdown

**Start:** `docker-compose start` → `k3d cluster start` → re-patch CoreDNS + node hosts
**Stop:** `k3d cluster stop` → `docker-compose stop` (NOT `down -v`)

**macOS add-on:** `colima start` before the Start sequence after a machine reboot.

## Platform detection (`scripts/lib/common.sh`)

- `PLATFORM` = `macos | linux | wsl | windows`
- `PLATFORM_ARCH` = `amd64 | arm64`
- `is_unix` helper returns 0 on `macos|linux|wsl`
- `COMPOSE_FILES` auto-adds `docker-compose.linux.yml` (dnsmasq `:53` drop) on all unix hosts
