# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [1.2.0] — 2026-04-17

### Added
- **BankOffer AI** (`bankoffer` namespace) — AI-powered banking personalization platform deployed via ArgoCD multi-source Helm Application:
  - FastAPI backend (`bankoffer-api:dev`) with DEMO_MODE, Redis cache, PostgreSQL seeded with 50 customers + 3827 transactions
  - Bitnami PostgreSQL (imported as `bankoffer-postgresql:dev`) + Redis sub-charts
  - Ingress at `http://bankoffer.local` via Traefik — health, offers, profiles, compliance endpoints live
  - Source: `platform/bankoffer-platform.git` with values from `platform/gitops-infra.git/manifests/bankoffer/values.yaml`
- **CareerForge** (`careerforge` namespace) — AI career management platform deployed via ArgoCD Kustomize Application:
  - FastAPI backend services: admin (port 8000), employee (port 8002), coach (port 8003), ai-connector (port 8001)
  - **Celery worker** + **Celery beat** connected to Redis — demonstrates async task pub-sub with `services.ai_connector.celery_app`
  - pgvector PostgreSQL (pgvector/pgvector:pg16) for vector search
  - 3 React/Vite nginx frontends (admin, coach, employee portals) at `cf-admin.local`, `cf-coach.local`, `cf-employee.local`
  - API gateway at `cf-api.local` routing `/admin`, `/employee`, `/coach` to respective FastAPI services
  - kustomize `landing-zone` overlay: Traefik ingress, Vault volumes removed, Ollama excluded (0 replicas), single uvicorn worker with asyncio loop
  - Source: `platform/careerforge.git/kustomize/overlays/landing-zone`
- ArgoCD repo credentials for `bankoffer-platform` and `careerforge` repos in `manifests/argocd/values.yaml`
- `/etc/hosts` entries: `bankoffer.local`, `cf-api.local`, `cf-admin.local`, `cf-coach.local`, `cf-employee.local` → 127.0.0.1

### Key Invariants Discovered
- **Bitnami Docker Hub images removed** — generic `:16` tags gone; must pre-pull from `registry-1.docker.io/bitnami/postgresql:latest` and import to k3d as local image
- **Careerforge nginx portals listen on 8080** (not 80) — service `targetPort` must match
- **k3d stdin image import unreliable** — use `docker exec -i <node> ctr images import -` per-node loop instead
- **MetalLB IPs not routable from WSL host** — all `/etc/hosts` entries must use `127.0.0.1` (tunneled through Docker port mapping)
- **BankOffer NetworkPolicy hardcoded for ingress-nginx** — disabled for landing zone (Traefik is in kube-system)

---

## [1.1.1] — 2026-04-17

### Added
- `gitops-repo/manifests/keycloak/configure-job.yaml` — ArgoCD PostSync hook Job that idempotently creates the Keycloak realm, OIDC clients (Gitea + ArgoCD), groups scope, and dev user after every keycloak app sync; uses `alpine:3.20` with `curl` + `python3`; deleted by ArgoCD on success

### Changed
- `gitops-repo/manifests/argocd/values.yaml`: ArgoCD OIDC config (`oidc.config`), RBAC policy, and Gitea repo credential are now declared in Git and managed by ArgoCD itself — no more `kubectl patch` outside of GitOps; removed deprecated `server.config`/`server.rbacConfig` in favour of `configs.cm`/`configs.rbac`
- `gitops-repo/manifests/keycloak/values.yaml`: moved `--proxy-headers=xforwarded` from `extraEnv` to the `command` array; removed `KC_PROXY_HEADERS`, `KC_HTTP_ENABLED`, `KC_HOSTNAME_STRICT` from `extraEnv` (duplicated chart defaults, caused ArgoCD `ComparisonError`)
- `scripts/10-configure-oidc.sh`: reduced to Gitea OIDC provider setup only (legitimately imperative — Gitea has no REST API for auth sources); waits for the Keycloak gitops realm to be reachable via OIDC discovery URL instead of polling a transient Job

### Fixed
- ArgoCD `selfHeal: true` was reverting every `kubectl patch argocd-cm` from the old phase 10 — OIDC config is now in `values.yaml` so ArgoCD self-manages its own OIDC instead of fighting the script
- `gitops-repo/manifests/argocd/values.yaml`: `configs.repositories.password` was the literal string `CHANGE_ME_from_env` — changed to `${GITEA_ADMIN_PASSWORD}` so `envsubst` in phase 07 injects the real credential; ArgoCD now self-manages its Gitea repo secret
- `keycloak/values.yaml` and `keycloak-postgres/secret.yaml`: duplicate `KC_PROXY_HEADERS` env var between chart defaults and `extraEnv` caused `ComparisonError` in ArgoCD strategic merge patch — removed duplicates from `extraEnv`

---

## [1.1.0] — 2026-04-17

### Added
- Platform detection (`detect_platform()` in `scripts/lib/common.sh`) — returns `wsl`, `linux`, or `windows`
- `PLATFORM` and `COMPOSE_FILES` variables exported from `common.sh` for use by all scripts
- `docker-compose/docker-compose.linux.yml` — override that removes the dnsmasq host-port-53 binding (conflicts with `systemd-resolved` on Ubuntu/WSL)
- `setup-hosts.sh`: Linux/WSL path writes all 7 service entries to `/etc/hosts` via `sudo`
- `configure-dns.sh`: Linux/WSL path skips the Windows NRPT/PowerShell block; validates `/etc/hosts` entries instead
- `00-prerequisites.sh`: downloads `argocd-linux-amd64` (not `.exe`) on Linux/WSL; installs `kubectl` via `curl` if missing on Linux instead of erroring out
- Bootstrap on Linux/WSL: run `bash scripts/bootstrap.sh` directly — no PowerShell wrapper needed

### Changed
- `02-start-gitea.sh`, `03-configure-gitea.sh`, `teardown.sh`: use `$COMPOSE_FILES` from `common.sh` instead of a hard-coded single `-f` flag so the Linux override is picked up automatically

### Fixed
- dnsmasq container fails to start on Ubuntu/Debian because `systemd-resolved` already holds port 53 — resolved by the Linux compose override
- `KUBECONFIG` env var inherited as literal string `${KUBECONFIG:-$HOME/.kube/config}` from shell profile poisoned all kubectl/helm calls — fixed by `unset KUBECONFIG` at the top of `common.sh`
- `04-create-k3d-cluster.sh`: replaced `kubectl config use-context` with `k3d kubeconfig merge --kubeconfig-merge-default --kubeconfig-switch-context` so the cluster context is always correctly written to `~/.kube/config`
- `05-install-metallb.sh`: increased helm `--wait` timeout from 120 s to 300 s (WSL Docker image pulls are slower than Windows Docker Desktop)
- `08-configure-argocd-repo.sh`: added `kubectl rollout restart deployment argocd-repo-server` after creating the Gitea credential secret — prevents the repo-server from caching an "authentication required" error
- `09-apply-app-of-apps.sh`: pre-applies the `allow-argocd-egress: {}` NetworkPolicy before the root ArgoCD app is synced, breaking the chicken-and-egg where the DNS-only egress policy blocked Helm chart downloads and kube-apiserver access required to deploy the policies themselves; also adds a wait-loop + hard-refresh if the root app stays `Unknown`
- `09b-build-portal.sh` (new): builds `landing-portal:latest` as `--platform linux/amd64` and imports it into all k3d nodes via `docker cp + ctr images import` tarball — multi-arch images fail `ctr import` directly; `imagePullPolicy: Never` requires the image to be pre-imported
- NetworkPolicy `allow-argocd-egress: {}` added to `gitops-repo/manifests/local-ingress/network-policies.yaml` — ArgoCD is the GitOps control plane and needs unrestricted egress
- NetworkPolicy `allow-keycloak-postgres`: `default-deny-ingress` in the keycloak namespace was blocking Keycloak from reaching its own postgres on port 5432
- NetworkPolicy `allow-traefik-ingress` and `allow-argocd-oidc` in keycloak namespace: pod selector fixed from `app.kubernetes.io/name: keycloak` to `app.kubernetes.io/name: keycloakx` (codecentric Helm chart label)
- `gitops-repo/manifests/keycloak/values.yaml`: admin password env var renamed from `KEYCLOAK_ADMIN_PASSWORD` to `KC_BOOTSTRAP_ADMIN_PASSWORD` (Keycloak 26.x deprecation); placeholder changed from `CHANGE_ME_from_env` to `${KEYCLOAK_ADMIN_PASSWORD}` so `envsubst` in phase 07 substitutes the actual credential from `.env`
- `gitops-repo/manifests/keycloak-postgres/secret.yaml`: password placeholder changed from `CHANGE_ME_from_env` to `${KEYCLOAK_DB_PASSWORD}` so `envsubst` substitutes the actual credential
- `10-configure-oidc.sh`: Keycloak 26.x does not create a `groups` client scope by default — script now creates it explicitly via the Admin API with a `oidc-group-membership-mapper`; switched from fragile `grep`-based JSON field extraction to `python3 -c` parsing; fixed `grep` no-match exit code killing the script under `set -eo pipefail` by adding `|| true`; fixed `--skip-local-2fa=true` → `--skip-local-2fa` (boolean flag syntax for Gitea CLI)

---

## [1.0.0] — 2026-04-16

Initial release of the GitOps Landing Zone.

### Added
- Fully reproducible local GitOps development environment for **Windows 11 + Docker Desktop + Git Bash**
- **k3d** cluster (`gitops-local`) — 1 server + 2 agents on Docker network `gitops` (172.20.0.0/24)
- **Docker Compose stack**: Gitea 1.22 + PostgreSQL 16, Gitea Act Runner, Vaultwarden, dnsmasq
- **MetalLB** LoadBalancer with IP pool 172.20.0.100–150
- **Traefik** ingress on ports 80/443 with `*.local` hostnames
- **ArgoCD** (app-of-apps pattern) syncing from self-hosted Gitea (`platform/gitops-infra`)
- **Keycloak + PostgreSQL** — OIDC SSO for Gitea and ArgoCD, realm `gitops`
- **cert-manager** and **Sealed Secrets** for TLS and encrypted secret storage
- **Prometheus + Grafana + Loki + Promtail** — full observability stack with pre-built dashboards and alert rules
- **Vaultwarden** — self-hosted Bitwarden-compatible password manager (HTTPS on port 8443)
- **Landing Portal** at `portal.local` with service index and agent federation page
- **27 NetworkPolicies** — deny-all-ingress + explicit Traefik allowlists per namespace
- 11 numbered bootstrap scripts (phases 00–10) + `bootstrap.ps1` elevated PowerShell wrapper
- `teardown.sh` + `teardown.ps1` for clean environment removal
- `backup.sh` and `restore.sh` for Gitea DB + Keycloak realms + Vaultwarden
- `configure-dns.sh` — Windows NRPT rule for `*.local` → `127.0.0.1`
- `configure-webhooks.sh` — Gitea → ArgoCD push webhooks (instant sync)
- `install-hooks.sh` + `pre-commit-hook.sh` — gitleaks + YAML lint + helm lint
- Gitea Actions CI workflow: YAML lint + helm lint + dry-run validate
- **AI Agent Federation**: 6 Claude Code agents (orchestrator, platform-infra, cluster-lifecycle, gitea-argocd, observability, security) coordinated via `.claude/team-state.json`
- **12 reusable skills**: `/kubectl-status`, `/argocd-sync`, `/docker-build-import`, `/helm-validate`, `/kubeseal-secret`, `/bootstrap-phase`, `/gitea-api`, `/keycloak-admin`, `/kustomize-build`, `/grafana-dashboard`, `/cluster-health`, `/netpol-test`
- `.env.example` with all required variables; `.gitleaks.toml` secret scanner config

[Unreleased]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/releases/tag/v1.0.0
