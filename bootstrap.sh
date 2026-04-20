#!/usr/bin/env bash
# Top-level bootstrap entry point for Linux/WSL/macOS.
#
#   ./bootstrap.sh
#
# Windows users: use bootstrap.ps1 from an elevated PowerShell instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNAME_S="$(uname -s 2>/dev/null || echo unknown)"

# ── Platform sanity check ─────────────────────────────────────────────────────
case "$UNAME_S" in
  Darwin)
    echo "[bootstrap] Detected macOS ($(uname -m))."
    # Colima must be running before we can call docker.
    if command -v colima &>/dev/null; then
      if ! colima status &>/dev/null; then
        echo "[bootstrap] Colima is not running. Start it first:"
        echo ""
        echo "    colima start --cpu 4 --memory 10 --disk 60"
        echo ""
        echo "  For Apple Silicon with Rosetta (x86_64 fallback):"
        echo "    colima start --cpu 4 --memory 10 --disk 60 --vm-type vz --vz-rosetta"
        echo ""
        exit 1
      fi
      echo "[bootstrap] Colima is running."
    elif ! docker info &>/dev/null; then
      echo "[bootstrap] No Colima and docker is not reachable."
      echo "[bootstrap] Install Colima (brew install colima) or start Docker Desktop."
      exit 1
    fi
    ;;
  Linux)
    echo "[bootstrap] Detected Linux ($(uname -m))."
    if ! docker info &>/dev/null; then
      echo "[bootstrap] Docker daemon not reachable. Start it with:"
      echo "    sudo systemctl start docker"
      exit 1
    fi
    ;;
  *)
    echo "[bootstrap] Unsupported platform: $UNAME_S"
    echo "[bootstrap] On Windows, use bootstrap.ps1 from an elevated PowerShell."
    exit 1
    ;;
esac

# ── .env check ────────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "[bootstrap] No .env found. Copy and fill in the template:"
  echo ""
  echo "    cp .env.example .env"
  echo "    # Edit .env, replace every CHANGE_ME_* with a strong secret"
  echo "    # (openssl rand -base64 24 for passwords, openssl rand -hex 24 for OIDC secrets)"
  echo ""
  exit 1
fi

# Hand off to the master orchestrator.
exec bash "$SCRIPT_DIR/scripts/bootstrap.sh"
