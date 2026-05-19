#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "ERROR: Must run as root"; exit 1; }

systemctl stop guardian 2>/dev/null || true
systemctl disable guardian 2>/dev/null || true
rm -f /etc/systemd/system/guardian.service
systemctl daemon-reload
pip3 uninstall guardiand -y 2>/dev/null || true

read -r -p "Remove data directories (/var/log/guardian, /var/lib/guardian)? [y/N] " answer
case "$answer" in
    [yY]|[yY][eE][sS])
        rm -rf /var/log/guardian /var/lib/guardian
        echo "Data directories removed."
        ;;
esac

echo "GuardianD uninstalled."
