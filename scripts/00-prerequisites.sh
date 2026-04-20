#!/usr/bin/env bash
# Install prerequisites: k3d, helm, argocd CLI, kubectl, kubeseal.
# Supports Linux/WSL (x86_64 + arm64) and macOS (x86_64 + Apple Silicon).
source "$(dirname "$0")/lib/common.sh"

# User-writable bin directory used as fallback when brew is unavailable.
USER_BIN="$HOME/bin"
mkdir -p "$USER_BIN"
export PATH="$USER_BIN:$PATH"

log_info "Checking prerequisites (platform: $PLATFORM, arch: $PLATFORM_ARCH)..."

# Map PLATFORM -> OS slug used by most upstream release URLs.
case "$PLATFORM" in
  macos)      OS_SLUG="darwin" ;;
  linux|wsl)  OS_SLUG="linux"  ;;
  *)          OS_SLUG="windows" ;;
esac

# Prefer brew on macOS — brew handles Apple Silicon binaries correctly.
HAS_BREW=0
if [[ "$PLATFORM" == "macos" ]] && command -v brew &>/dev/null; then
  HAS_BREW=1
fi

# ── Docker engine probe ───────────────────────────────────────────────────────
# Colima (macOS) or Docker Desktop / Docker Engine (Linux) must be running.
if ! docker info &>/dev/null; then
  log_error "Docker engine is not reachable."
  if [[ "$PLATFORM" == "macos" ]]; then
    log_error "  Start Colima first:  colima start --cpu 4 --memory 10 --disk 60"
    log_error "  Or start Docker Desktop."
  else
    log_error "  Start the docker daemon: sudo systemctl start docker"
  fi
  exit 1
fi
log_ok "Docker engine reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'unknown'))"

# ── Raise kernel watch/file limits inside the Colima VM ────────────────────────
# Promtail (monitoring stack) crashes with "too many open files" on a busy
# cluster unless fs.inotify.max_user_watches is raised. Apply idempotently.
if [[ "$PLATFORM" == "macos" ]] && command -v colima &>/dev/null; then
  log_info "Raising inotify + file limits inside the Colima VM..."
  colima ssh -- sudo sh -c '
    cat > /etc/sysctl.d/99-k3d-inotify.conf <<EOF
fs.inotify.max_user_watches   = 524288
fs.inotify.max_user_instances = 512
fs.file-max                   = 524288
EOF
    sysctl --system >/dev/null 2>&1 || true
    sysctl -p /etc/sysctl.d/99-k3d-inotify.conf >/dev/null 2>&1 || true
  ' 2>/dev/null && log_ok "Colima VM inotify limits set (persists across reboot)" \
                || log_warn "Could not tune Colima VM sysctls (non-fatal; promtail may crash)"
fi

# ── Validate .env password safety ──────────────────────────────────────────────
# Passwords with '+', '/', '=' break form-urlencoded OAuth2 token requests
# (notably the Keycloak admin-cli call in configure-job). Fail fast with a
# clear hint pointing at scripts/gen-env.sh.
validate_env_pw() {
  local name="$1" val="${!1:-}"
  if [[ -n "$val" && "$val" =~ [+/=] ]]; then
    log_error "$name in .env contains '+', '/' or '=' — these break OAuth form-urlencoded bodies."
    log_error "  Regenerate with: bash scripts/gen-env.sh --force  (URL-safe alnum output)"
    return 1
  fi
  return 0
}
BAD=0
for v in GITEA_ADMIN_PASSWORD GITEA_DB_PASSWORD ARGOCD_ADMIN_PASSWORD \
         KEYCLOAK_ADMIN_PASSWORD KEYCLOAK_DB_PASSWORD GRAFANA_ADMIN_PASSWORD; do
  validate_env_pw "$v" || BAD=1
done
if [[ "$BAD" -ne 0 ]]; then
  exit 1
fi
log_ok ".env passwords are URL-safe"

# ── docker compose plugin (scripts use `docker compose` subcommand) ───────────
# On macOS with brew's docker-compose package, only the standalone binary ships
# — the v2 CLI plugin is not wired. Symlink it into ~/.docker/cli-plugins so
# `docker compose ...` works everywhere the scripts invoke it.
if ! docker compose version &>/dev/null; then
  log_info "'docker compose' plugin not wired — attempting to install..."
  if [[ "$HAS_BREW" -eq 1 ]]; then
    if ! command -v docker-compose &>/dev/null; then
      brew install docker-compose
    fi
    mkdir -p "$HOME/.docker/cli-plugins"
    DC_BIN="$(command -v docker-compose)"
    if [[ -n "$DC_BIN" ]]; then
      ln -sf "$DC_BIN" "$HOME/.docker/cli-plugins/docker-compose"
    fi
  fi
  if ! docker compose version &>/dev/null; then
    log_error "'docker compose' subcommand still unavailable."
    log_error "  Install the v2 plugin manually: https://docs.docker.com/compose/install/"
    exit 1
  fi
fi
log_ok "docker compose: $(docker compose version --short 2>/dev/null || docker compose version | head -1)"

# ── envsubst (used by 05/06/07/09 to render templates) ───────────────────────
# macOS doesn't ship envsubst; it comes from the gettext brew package.
if ! command -v envsubst &>/dev/null; then
  if [[ "$HAS_BREW" -eq 1 ]]; then
    log_info "Installing gettext (provides envsubst) via brew..."
    brew install gettext
  else
    log_error "envsubst is required but not found. Install gettext for your distro."
    log_error "  Debian/Ubuntu: sudo apt-get install -y gettext-base"
    log_error "  RHEL/Fedora:   sudo dnf install -y gettext"
    exit 1
  fi
fi
log_ok "envsubst available: $(command -v envsubst)"

# ── k3d ───────────────────────────────────────────────────────────────────────
if ! command -v k3d &>/dev/null; then
  if [[ "$HAS_BREW" -eq 1 ]]; then
    log_info "Installing k3d via brew..."
    brew install k3d
  else
    log_info "Installing k3d..."
    curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | USE_SUDO=false K3D_INSTALL_DIR="$USER_BIN" bash
  fi
  log_ok "k3d installed: $(k3d version 2>/dev/null | head -1)"
else
  log_ok "k3d already installed: $(k3d version 2>/dev/null | head -1)"
fi

# ── helm (pinned — v3.20.x has a Go runtime crash bug) ────────────────────────
HELM_WANT="v3.17.3"
install_helm_manual() {
  # Fallback installer used when brew is unavailable (Linux) or unwanted.
  curl -s https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
    | USE_SUDO=false HELM_INSTALL_DIR="$USER_BIN" DESIRED_VERSION="$HELM_WANT" bash
}
if ! command -v helm &>/dev/null; then
  log_info "Installing helm ${HELM_WANT}..."
  if [[ "$HAS_BREW" -eq 1 ]]; then
    brew install helm
  else
    install_helm_manual
  fi
  log_ok "helm installed: $(helm version --short)"
else
  HELM_CUR="$(helm version --short 2>/dev/null)"
  if [[ "$HELM_CUR" == *"3.20"* ]]; then
    log_warn "Helm $HELM_CUR has a known crash bug, downgrading to ${HELM_WANT}..."
    install_helm_manual
    log_ok "helm reinstalled: $(helm version --short)"
  else
    log_ok "helm already installed: $HELM_CUR"
  fi
fi

# ── argocd CLI ────────────────────────────────────────────────────────────────
if ! command -v argocd &>/dev/null; then
  log_info "Installing argocd CLI..."
  if [[ "$HAS_BREW" -eq 1 ]]; then
    brew install argocd
  else
    ARGOCD_VERSION=$(curl -s https://api.github.com/repos/argoproj/argo-cd/releases/latest | grep tag_name | cut -d '"' -f 4)
    if [[ "$PLATFORM" == "windows" ]]; then
      ARGOCD_BIN="argocd-windows-amd64.exe"
      ARGOCD_OUT="$USER_BIN/argocd.exe"
    else
      ARGOCD_BIN="argocd-${OS_SLUG}-${PLATFORM_ARCH}"
      ARGOCD_OUT="$USER_BIN/argocd"
    fi
    curl -sSL -o "$ARGOCD_OUT" \
      "https://github.com/argoproj/argo-cd/releases/download/${ARGOCD_VERSION}/${ARGOCD_BIN}"
    chmod +x "$ARGOCD_OUT"
  fi
  log_ok "argocd CLI installed: $(argocd version --client --short 2>/dev/null || echo 'installed')"
else
  log_ok "argocd CLI already installed: $(argocd version --client --short 2>/dev/null || echo 'present')"
fi

# ── kubectl ───────────────────────────────────────────────────────────────────
if ! command -v kubectl &>/dev/null; then
  if [[ "$HAS_BREW" -eq 1 ]]; then
    log_info "Installing kubectl via brew..."
    brew install kubectl
  elif [[ "$PLATFORM" == "linux" || "$PLATFORM" == "wsl" || "$PLATFORM" == "macos" ]]; then
    log_info "Installing kubectl (${OS_SLUG}/${PLATFORM_ARCH})..."
    KUBECTL_VERSION=$(curl -sL https://dl.k8s.io/release/stable.txt)
    curl -sSL -o "$USER_BIN/kubectl" \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/${OS_SLUG}/${PLATFORM_ARCH}/kubectl"
    chmod +x "$USER_BIN/kubectl"
  else
    log_error "kubectl is required but not found. Install it from https://kubernetes.io/docs/tasks/tools/"
    exit 1
  fi
  log_ok "kubectl installed: $(kubectl version --client 2>/dev/null | head -1)"
else
  log_ok "kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1)"
fi

# ── kubeseal (optional but used by /kubeseal-secret skill) ────────────────────
if ! command -v kubeseal &>/dev/null; then
  log_info "Installing kubeseal..."
  if [[ "$HAS_BREW" -eq 1 ]]; then
    brew install kubeseal
    log_ok "kubeseal installed: $(kubeseal --version 2>/dev/null || echo 'present')"
  else
    KS_VERSION=$(curl -s https://api.github.com/repos/bitnami-labs/sealed-secrets/releases/latest \
      | grep tag_name | cut -d '"' -f 4 | sed 's/^v//')
    if [[ -n "$KS_VERSION" ]]; then
      curl -sSL -o /tmp/kubeseal.tar.gz \
        "https://github.com/bitnami-labs/sealed-secrets/releases/download/v${KS_VERSION}/kubeseal-${KS_VERSION}-${OS_SLUG}-${PLATFORM_ARCH}.tar.gz"
      tar -xzf /tmp/kubeseal.tar.gz -C "$USER_BIN" kubeseal 2>/dev/null || true
      chmod +x "$USER_BIN/kubeseal" 2>/dev/null || true
      rm -f /tmp/kubeseal.tar.gz
      log_ok "kubeseal installed: $(kubeseal --version 2>/dev/null || echo 'present')"
    else
      log_warn "Could not resolve latest kubeseal version — install manually if /kubeseal-secret is needed"
    fi
  fi
else
  log_ok "kubeseal already installed: $(kubeseal --version 2>/dev/null | head -1)"
fi

# ── helm repos ────────────────────────────────────────────────────────────────
log_info "Adding helm repositories..."
helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
helm repo add metallb https://metallb.github.io/metallb 2>/dev/null || true
helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets 2>/dev/null || true
helm repo update || log_warn "helm repo update had issues (non-fatal, continuing)"
log_ok "Helm repos configured"

log_ok "All prerequisites satisfied"
