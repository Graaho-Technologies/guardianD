#!/usr/bin/env bash
# GuardianD uninstall script

set -euo pipefail

REMOVE_DATA=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove-data) REMOVE_DATA=true; shift ;;
    *) shift ;;
  esac
done

systemctl stop guardian 2>/dev/null || true
systemctl disable guardian 2>/dev/null || true
rm -f /etc/systemd/system/guardian.service
systemctl daemon-reload
pip uninstall -y guardiand 2>/dev/null || true
rm -f /etc/guardian/guardian.yaml
echo "✓ GuardianD service removed"

if [[ "$REMOVE_DATA" == "true" ]]; then
  rm -rf /var/log/guardian /var/lib/guardian
  echo "✓ Data directories removed"
fi

echo "GuardianD uninstalled."
