#!/usr/bin/env bash
# Top-level teardown entry point for Linux/WSL/macOS.
#
#   ./teardown.sh
#
# Windows users: use teardown.ps1 from an elevated PowerShell instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/scripts/teardown.sh"
