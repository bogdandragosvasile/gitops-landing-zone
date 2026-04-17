#!/usr/bin/env bash
# install-hooks.sh — Install shared pre-commit hook into all three GitOps repos
#
# Usage:
#   bash scripts/install-hooks.sh
#
# Idempotent: safe to re-run at any time. Re-running will refresh the hook
# symlink/copy to pick up changes to pre-commit-hook.sh.
#
# Repos targeted:
#   - gitops-repo             (this repo)
#   - <your-app>/       (additional app repos)
#   - # Add more REPOS entries as you fork in new apps
#
# The script resolves repo paths relative to the parent directory of the
# directory that contains this script. That way it works regardless of which
# repo you run it from, as long as the three repos share a common parent.

set -euo pipefail

# ──────────────────────────────────────────────
# Resolve paths
# ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ lives inside AI_Local_Dev/, so REPO_ROOT is one level up
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HOOK_SOURCE="${SCRIPT_DIR}/pre-commit-hook.sh"

# The three repos (relative to REPO_ROOT)
TARGET_REPOS=(
  "gitops-repo"
)

# ──────────────────────────────────────────────
# Colour helpers
# ──────────────────────────────────────────────
RED=""
YEL=""
GRN=""
BLU=""
RST=""
if [ -t 1 ]; then
  RED="\033[0;31m"
  YEL="\033[0;33m"
  GRN="\033[0;32m"
  BLU="\033[0;34m"
  RST="\033[0m"
fi

info()    { printf "${BLU}[install-hooks]${RST} %s\n" "$*"; }
success() { printf "${GRN}[install-hooks] OK:${RST} %s\n" "$*"; }
warn()    { printf "${YEL}[install-hooks] WARN:${RST} %s\n" "$*"; }
error()   { printf "${RED}[install-hooks] ERROR:${RST} %s\n" "$*" >&2; }

# ──────────────────────────────────────────────
# Verify the hook source exists
# ──────────────────────────────────────────────
if [ ! -f "$HOOK_SOURCE" ]; then
  error "Hook source not found: ${HOOK_SOURCE}"
  error "Expected pre-commit-hook.sh in the same directory as this script."
  exit 1
fi

# Ensure the source hook is executable
chmod +x "$HOOK_SOURCE"
info "Hook source: ${HOOK_SOURCE}"
info "Repo root:   ${REPO_ROOT}"
echo ""

# ──────────────────────────────────────────────
# Install loop
# ──────────────────────────────────────────────
INSTALLED=0
SKIPPED=0
ERRORS=0

for repo_rel in "${TARGET_REPOS[@]}"; do
  REPO_PATH="${REPO_ROOT}/${repo_rel}"
  HOOK_DIR="${REPO_PATH}/.git/hooks"
  HOOK_DEST="${HOOK_DIR}/pre-commit"

  info "Processing: ${repo_rel}"

  # ── Verify .git directory exists ──────────────────────────────────────────
  if [ ! -d "${REPO_PATH}/.git" ]; then
    warn "  .git directory not found at '${REPO_PATH}' — skipping."
    warn "  (repo may not exist yet; run install-hooks.sh again after cloning)"
    SKIPPED=$((SKIPPED + 1))
    echo ""
    continue
  fi

  # ── Ensure hooks directory exists ─────────────────────────────────────────
  if [ ! -d "$HOOK_DIR" ]; then
    mkdir -p "$HOOK_DIR"
    info "  Created hooks directory: ${HOOK_DIR}"
  fi

  # ── Handle existing hook ───────────────────────────────────────────────────
  if [ -f "$HOOK_DEST" ] || [ -L "$HOOK_DEST" ]; then
    # Check if it's already our hook (idempotency check)
    if [ -L "$HOOK_DEST" ]; then
      CURRENT_TARGET=$(readlink "$HOOK_DEST" 2>/dev/null || echo "")
      if [ "$CURRENT_TARGET" = "$HOOK_SOURCE" ]; then
        success "  Already installed (symlink up to date): ${HOOK_DEST}"
        INSTALLED=$((INSTALLED + 1))
        echo ""
        continue
      fi
    fi

    # Back up the existing hook before overwriting
    BACKUP="${HOOK_DEST}.bak.$(date +%Y%m%d%H%M%S)"
    info "  Existing hook found — backing up to: ${BACKUP}"
    cp "$HOOK_DEST" "$BACKUP" 2>/dev/null || mv "$HOOK_DEST" "$BACKUP"
  fi

  # ── Install the hook ───────────────────────────────────────────────────────
  # Try a symlink first (preferred — any update to the source is instantly
  # reflected in all repos without re-running this script).
  # On Windows/Git Bash, symlink creation may require elevated privileges or
  # Developer Mode; if it fails, fall back to a hard copy.

  INSTALL_METHOD=""
  if ln -sf "$HOOK_SOURCE" "$HOOK_DEST" 2>/dev/null; then
    INSTALL_METHOD="symlink"
  else
    # Symlink failed (common on Windows without Developer Mode) — copy instead
    cp "$HOOK_SOURCE" "$HOOK_DEST"
    INSTALL_METHOD="copy"
    warn "  Symlink not supported — installed as a copy."
    warn "  Re-run install-hooks.sh after updating pre-commit-hook.sh to refresh copies."
  fi

  # ── Make executable ────────────────────────────────────────────────────────
  chmod +x "$HOOK_DEST"

  # ── Verify the hook is runnable ────────────────────────────────────────────
  if [ -x "$HOOK_DEST" ]; then
    success "  Installed (${INSTALL_METHOD}): ${HOOK_DEST}"
    INSTALLED=$((INSTALLED + 1))
  else
    error "  Hook installed but is not executable: ${HOOK_DEST}"
    ERRORS=$((ERRORS + 1))
  fi

  echo ""
done

# ──────────────────────────────────────────────
# Print summary
# ──────────────────────────────────────────────
echo "────────────────────────────────────────"
printf "${GRN}Installed:${RST} %d repo(s)\n" "$INSTALLED"
if [ "$SKIPPED" -gt 0 ]; then
  printf "${YEL}Skipped:${RST}   %d repo(s) (not found)\n" "$SKIPPED"
fi
if [ "$ERRORS" -gt 0 ]; then
  printf "${RED}Errors:${RST}    %d repo(s)\n" "$ERRORS"
fi
echo "────────────────────────────────────────"
echo ""

if [ "$ERRORS" -gt 0 ]; then
  error "One or more hooks failed to install. See messages above."
  exit 1
fi

if [ "$INSTALLED" -gt 0 ]; then
  info "The pre-commit hook will now run automatically on every 'git commit'."
  info "To test without committing: bash ${HOOK_SOURCE}"
  info "To bypass in an emergency:  git commit --no-verify"
fi

exit 0
