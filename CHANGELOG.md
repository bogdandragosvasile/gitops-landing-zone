# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] — 1.1.0

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

---

## [1.0.0] — 2026-04-17

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

[Unreleased]: https://github.com/bogdandragosvasile/gitops-landing-zone/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bogdandragosvasile/gitops-landing-zone/releases/tag/v1.0.0
