#!/usr/bin/env bash
# GuardianD install script
# Usage: bash scripts/install.sh [--config /path/to/config.yaml]

set -euo pipefail

GUARDIAN_USER="root"
CONFIG_DIR="/etc/guardian"
LOG_DIR="/var/log/guardian"
DATA_DIR="/var/lib/guardian"
RUN_DIR="/var/run/guardian"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_ARG="${1:-}"
CONFIG_PATH="${CONFIG_DIR}/guardian.yaml"

# Parse --config argument
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# 1. Check Python >= 3.9
python_bin=$(command -v python3 || command -v python || true)
if [[ -z "$python_bin" ]]; then
  echo "ERROR: Python 3 not found" >&2; exit 1
fi
py_version=$("$python_bin" -c "import sys; print(sys.version_info >= (3,9))")
if [[ "$py_version" != "True" ]]; then
  echo "ERROR: Python 3.9+ required" >&2; exit 1
fi
echo "✓ Python OK: $("$python_bin" --version)"

# 2. Install package
if [[ -f "pyproject.toml" ]]; then
  echo "Installing from source..."
  pip install -e . --quiet
else
  echo "Installing from PyPI..."
  pip install guardiand --quiet
fi
echo "✓ GuardianD installed"

# 3. Create directories
for dir in "$CONFIG_DIR" "$LOG_DIR" "$DATA_DIR" "$RUN_DIR"; do
  mkdir -p "$dir"
  echo "✓ Created $dir"
done

# 4. Copy example config if none exists
if [[ ! -f "$CONFIG_PATH" ]]; then
  if [[ -f "config/guardian.example.yaml" ]]; then
    cp config/guardian.example.yaml "$CONFIG_PATH"
    echo "✓ Config copied to $CONFIG_PATH — edit before starting"
  else
    guardiand init --output "$CONFIG_PATH" || true
    echo "✓ Config generated at $CONFIG_PATH — edit before starting"
  fi
else
  echo "✓ Config already exists at $CONFIG_PATH"
fi

# 5. Install systemd unit
if [[ -f "systemd/guardian.service" ]]; then
  cp systemd/guardian.service "$SYSTEMD_DIR/guardian.service"
  systemctl daemon-reload
  echo "✓ Systemd unit installed"
fi

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_PATH and configure your alert channels"
echo "  2. systemctl enable --now guardian"
echo "  3. guardianctl status"
