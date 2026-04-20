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

# ── .env auto-generation ──────────────────────────────────────────────────────
# If no .env is present, generate one with URL-safe random secrets. This keeps
# the top-level entry point fully hands-off — no manual editing required.
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "[bootstrap] No .env found — generating one with URL-safe random secrets..."
  bash "$SCRIPT_DIR/scripts/gen-env.sh"
  echo "[bootstrap] .env created. Review $SCRIPT_DIR/.env if you want to inspect the generated secrets."
fi

# Hand off to the master orchestrator.
exec bash "$SCRIPT_DIR/scripts/bootstrap.sh"
