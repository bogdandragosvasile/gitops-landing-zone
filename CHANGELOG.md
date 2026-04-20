# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [1.4.0] — 2026-04-20

### Highlights
Fully hands-off bootstrap on macOS (Apple Silicon + Intel) via Colima. `git clone && ./bootstrap.sh` now succeeds on a fresh machine with no manual `.env` edits, no TTY required for sudo (GUI admin prompt fallback), and no hard-coded secrets leaking into the landing-zone git history. The BankOffer + CareerForge test workloads have been extracted to the sibling repo [`my-testing-apps`](https://github.com/bogdandragosvasile/my-testing-apps) so this repo is a clean base platform.

### Added
- **macOS + Apple Silicon (Colima) support** — platform detection in `scripts/lib/common.sh` returns `macos`; `PLATFORM_ARCH` auto-detects `amd64`/`arm64`; `is_unix` helper covers Linux/WSL/macOS uniformly; `COMPOSE_FILES` auto-applies `docker-compose.linux.yml` (dnsmasq `:53` drop) on all unix hosts.
- **`bootstrap.sh` / `teardown.sh`** — top-level bash wrappers for Linux/WSL/macOS. `bootstrap.sh` does a Colima liveness check on macOS and auto-invokes `gen-env.sh` if `.env` is missing.
- **`scripts/gen-env.sh`** — renders `.env` from `.env.example` with URL-safe random secrets on first bootstrap. All passwords are alphanumeric (no `+`, `/`, `=`) because Keycloak's admin-cli form-urlencoded token request decodes `+` as space; earlier bootstraps failed here with `invalid_grant`.
- **`scripts/01b-ensure-certs.sh`** — generates Vaultwarden self-signed TLS certs (`docker-compose/certs/vault.{crt,key}`, 10-year validity, SAN `localhost,vault.local,127.0.0.1`) if missing. Fixes vaultwarden compose startup on a fresh clone.
- **Colima VM sysctl tuning** (`scripts/00-prerequisites.sh`) — writes `/etc/sysctl.d/99-k3d-inotify.conf` inside the Colima VM (`fs.inotify.max_user_watches=524288`, `max_user_instances=512`, `file-max=524288`). Fixes promtail `CrashLoopBackOff: too many open files` on a busy cluster.
- **`scripts/04-create-k3d-cluster.sh` Colima DNS patch** — on macOS, rewrites each k3d node's `/etc/resolv.conf` to `8.8.8.8`/`1.1.1.1`. Colima's internal `192.168.5.2` resolver is unreachable from the nested `gitops` Docker network; without this, every image pull fails with `dial tcp: lookup <host>: Try again`.
- **Intra-namespace NetworkPolicy** `allow-intra-keycloak-http` — lets the PostSync keycloak-configure Job reach `keycloakx:8080` in the same namespace (blocked by `default-deny-ingress` previously).
- **Prometheus ingress NetworkPolicy** `allow-traefik-prometheus-ingress` — fixes 502 on `prometheus.local` (the existing `allow-traefik-ingress` rule only covered Grafana:3000).
- **Bcrypt-based ArgoCD admin password rotation** — `scripts/06-install-argocd.sh` now patches `argocd-secret.admin.password` with a bcrypt hash of `ARGOCD_ADMIN_PASSWORD` (htpasswd first, `python3 bcrypt` fallback). Zero network dependency; the previous `argocd login` + CLI flow regularly failed on fresh installs.
- **`docs/ADD_YOUR_APP.md`** + **`gitops-repo/apps-examples/my-app.yaml.example`** — step-by-step recipe and copy-paste Application template for deploying a new workload on top of the landing zone.
- **Tested-platforms matrix** in README.md; **Platform-specific notes** section covering DNS patch, inotify tuning, `.local` mDNS quirks, Docker CLI plugin symlink, and WSL2 specifics.

### Changed
- **Clean base platform** — moved BankOffer AI + CareerForge test applications out to the sibling repo `my-testing-apps`. The landing zone no longer ships any bespoke workloads:
  - Removed `app-repos/`, `gitops-repo/apps/{bankoffer,bankoffer-slides,careerforge,careerforge-slides}.yaml`, `gitops-repo/manifests/{bankoffer,bankoffer-slides,careerforge-slides}/`.
  - Dropped `scripts/07b-push-app-repos.sh` and `scripts/09c-build-app-images.sh` (phases 07b/09c) from the master bootstrap.
  - Removed bespoke `bankoffer.local`/`cf-*.local` entries from `setup-hosts.sh` and the CoreDNS NodeHosts patch.
  - Stripped the BankOffer AI + CareerForge OIDC client blocks from `keycloak/configure-job.yaml` (core Gitea/ArgoCD clients remain).
  - Replaced BankOffer + CareerForge cards in the landing portal with a single "Your App Here" example card.
- **`scripts/07-push-gitops-repo.sh`** — renders `gitops-repo/` into a fresh `mktemp -d`, pushes that rendered tree to Gitea, and leaves the source checked into the landing-zone repo as pristine `${VAR}` templates. Previous implementation ran envsubst in place and `git add -A` then baked resolved secrets into subsequent commits; re-generating `.env` stopped taking effect for helm-installed components whose values had drifted into hard-coded form.
- **`scripts/10-configure-oidc.sh`** — explicitly applies the keycloak-configure Job using the same `ENVSUBST_VARS` whitelist as phase 07, waits for `condition=Complete`, then probes the realm from inside the keycloakx pod. Previous implementation relied on the Argo PostSync hook (which self-deletes under `HookSucceeded`) and a plain `envsubst` call that clobbered shell vars (`$KC_URL`, `$KC_ADMIN_USER`) inside the script body, producing a configure script that hung forever on an empty URL.
- **`scripts/02-start-gitea.sh`** — also brings up `vaultwarden` and `dnsmasq` (previously only `gitea-db` + `gitea`).
- **`scripts/setup-hosts.sh`** — on macOS without a TTY, falls back to `osascript with administrator privileges` (native GUI prompt) instead of silently failing on `sudo`.
- **`scripts/09b-build-portal.sh`** — import bug fixes: use `ctr -n k8s.io images import` (previously imported into a nonexistent default containerd namespace); filter k3d nodes by runtime label instead of the non-existent `k3d node list --cluster` flag (k3d 5.8.x).
- **`scripts/00-prerequisites.sh`** — brew-aware installs on macOS (k3d, helm, kubectl, argocd, kubeseal, gettext); symlinks `docker-compose` into `~/.docker/cli-plugins/` so the v2 subcommand form works when brew ships only the standalone binary; arch-aware release URLs for non-brew hosts; validates `.env` password safety before proceeding.
- **`scripts/06-install-argocd.sh`** — admin password now set via offline bcrypt patch of `argocd-secret` (see Added section); old CLI-login flow removed.
- **NetworkPolicy** `allow-apiserver-webhook` in `metallb-system` now targets the correct chart labels (`app.kubernetes.io/name: metallb`, `app.kubernetes.io/component: controller`); previous `app: metallb, component: controller` selector matched nothing → apiserver could never reach the webhook → every IPAddressPool apply returned 502.
- **Template hygiene** — `argocd/values.yaml`, `keycloak/values.yaml`, `keycloak/configure-job.yaml`, `keycloak-postgres/secret.yaml` revert secrets and IDs back to `${VAR}` placeholders so `07-push-gitops-repo.sh`'s envsubst step is a clean first-run render.
- **Image build platform** — `scripts/09b-build-portal.sh` and any remaining builders pass `--platform linux/${PLATFORM_ARCH}` so images stay single-arch and `ctr import` on k3d nodes succeeds (multi-arch manifests silently break import).
- **Docs** — README.md rewritten to lead with Linux/macOS first; prerequisites split into three explicit platform blocks; quick-start is now `./bootstrap.sh` with the ten automated phases enumerated; new Platform-specific notes section covers every gotcha we hit.

### Fixed
- Bootstrap `keycloak-postgres-secret` hard-coded password caused new `.env` values to be silently ignored — postgres would come up with one password and the keycloakx StatefulSet with another, crashlooping forever with `FATAL: password authentication failed for user "keycloak"`.
- macOS `/etc/hosts` sudo path failed under non-interactive bootstrap (no TTY → `sudo: a terminal is required to read the password`); now detected and routed through `osascript`.
- macOS BSD grep lacks `-P`; `scripts/pre-commit-hook.sh` auto-detects `ggrep` and logs a warning if neither is available.
- Top-level `bootstrap.sh` wrapper no longer hard-fails when `.env` is missing — it triggers `scripts/gen-env.sh` automatically.

---

## [1.3.0] — 2026-04-17

### Added
- **`scripts/07b-push-app-repos.sh`** — new bootstrap phase that iterates `app-repos/` and pushes each subdirectory to a Gitea repo under the platform org; creates the repo via Gitea API if missing; called as step 07b in `bootstrap.sh`
- **`scripts/09c-build-app-images.sh`** — new bootstrap phase that builds and imports all application images into every k3d node using the `docker save | docker cp | ctr images import` tarball method (multi-arch workaround); handles bankoffer-slides, careerforge-slides, bankoffer-api, bankoffer-postgresql, and careerforge-* images
- **`app-repos/bankoffer-platform/`** — bankoffer-platform source tree (Dockerfile + Helm chart) bundled in the repo so `07b` can seed Gitea and `09c` can build `bankoffer-api:dev` from source on a fresh clone
- **`app-repos/careerforge/`** — careerforge kustomize manifests bundled in the repo so `07b` can seed Gitea and ArgoCD can sync the `careerforge` application
- **`gitops-repo/manifests/bankoffer-slides/src/`** — slide presentation source files (Dockerfile + HTML) so `bankoffer-slides:dev` can be built deterministically from source in `09c`
- **`gitops-repo/manifests/careerforge-slides/src/`** — slide presentation source files (Dockerfile + nginx HTML) so `careerforge-slides:dev` can be built from source in `09c`
- **Keycloak `bankofferai-app` OIDC client** in `gitops-repo/manifests/keycloak/configure-job.yaml` — public PKCE client (`pkce.code.challenge.method: S256`) for the BankOffer Customer Portal; created idempotently by the PostSync Job alongside existing Gitea/ArgoCD clients
- **App hostnames in `scripts/setup-hosts.sh`**: `bankoffer.local`, `bankoffer-slides.local`, `cf-admin.local`, `cf-coach.local`, `cf-employee.local`, `cf-slides.local` added to ENTRIES so they resolve from the WSL/Linux host on a fresh bootstrap
- **CoreDNS NodeHosts expanded in `scripts/04-create-k3d-cluster.sh`**: all 12 service hostnames (including app domains) are now patched into the CoreDNS NodeHosts configmap at cluster creation time so pods can resolve them immediately
- **NetworkPolicy `allow-monitoring-internal-egress`** in `gitops-repo/manifests/local-ingress/network-policies.yaml` — explicit egress rule allowing all pods in the `monitoring` namespace to reach each other (Grafana → Prometheus port 9090, Prometheus → Alertmanager, etc.)
- **NetworkPolicy ipBlock rules for node-level scrape targets** in `allow-prometheus-scrape-egress` — egress to `172.20.0.0/24` and `10.42.0.0/16` on ports 9100 (node-exporter), 10250 (kubelet), 9153 (CoreDNS metrics) so Prometheus can scrape host-network pods that bind to node IPs rather than pod IPs

### Changed
- `scripts/bootstrap.sh`: added steps `07b-push-app-repos.sh` and `09c-build-app-images.sh`; expanded the post-bootstrap URL summary to include BankOffer AI and CareerForge service URLs

### Fixed
- **Grafana "No Data" root cause** — `allow-dns-egress` (policyTypes: [Egress], podSelector: {}) implicitly denied all monitoring pod egress except port 53/UDP, blocking Grafana from reaching Prometheus. Fixed by adding `allow-monitoring-internal-egress` to permit intra-namespace egress.
- **Prometheus unable to scrape kubelet/node-exporter/CoreDNS** — these targets bind to node IPs (hostNetwork), not pod IPs, so `namespaceSelector: {}` alone doesn't permit egress to them. Fixed by adding ipBlock CIDR rules to `allow-prometheus-scrape-egress`.
- **Bootstrap not reproducible from a fresh clone** — app source, slide sources, and image build steps were missing. A new clone would have no `app-repos/` content, no slide Dockerfiles, and no scripts to build or import images, leaving pods in `ImagePullBackOff` or `Pending`. All gaps closed by the additions above.

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

[Unreleased]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/releases/tag/v1.0.0
