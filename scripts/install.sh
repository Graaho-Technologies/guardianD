#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="/etc/guardian"
LOG_DIR="/var/log/guardian"
DATA_DIR="/var/lib/guardian"
SYSTEMD_DIR="/etc/systemd/system"
INSTALL_FULL=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --full) INSTALL_FULL=1; shift ;;
        *) shift ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "ERROR: Must run as root"; exit 1; }
python3 -c "import sys; assert sys.version_info >= (3,9)" || { echo "Python 3.9+ required"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Install from the local clone (guardiand is not published to PyPI).
[[ $INSTALL_FULL -eq 1 ]] && pip3 install "$REPO_ROOT[full]" || pip3 install "$REPO_ROOT"

mkdir -p "$CONFIG_DIR" "$LOG_DIR" "$DATA_DIR"
chmod 750 "$CONFIG_DIR" "$LOG_DIR" "$DATA_DIR"

[[ ! -f "$CONFIG_DIR/guardian.yaml" ]] && {
    guardianctl init --output "$CONFIG_DIR/guardian.yaml"
    echo "Created config: $CONFIG_DIR/guardian.yaml — edit before starting."
}

cp "$REPO_ROOT/systemd/guardian.service" "$SYSTEMD_DIR/"
systemctl daemon-reload
systemctl enable guardian

echo ""
echo "GuardianD installed. Next: edit /etc/guardian/guardian.yaml, then:"
echo "  sudo systemctl start guardian && guardianctl status"
