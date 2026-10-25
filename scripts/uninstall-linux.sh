#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${SYNFULL_INSTALL_DIR:-/opt/synfull}"
STATE_DIR="${SYNFULL_STATE_DIR:-/var/lib/synfull}"
SERVICE_USER="${SYNFULL_SERVICE_USER:-synfull}"
PURGE=false
if [[ "${1:-}" == "--purge-data" ]]; then PURGE=true; elif [[ $# -gt 0 ]]; then echo 'Usage: sudo ./scripts/uninstall-linux.sh [--purge-data]' >&2; exit 2; fi
[[ "$(uname -s)" == Linux && $EUID -eq 0 ]] || { echo 'Run on Linux as root.' >&2; exit 1; }
if systemctl list-unit-files synfull.service --no-legend 2>/dev/null | grep -q synfull.service; then
  systemctl disable --now synfull.service || true
fi
rm -f /etc/systemd/system/synfull.service
systemctl daemon-reload
rm -rf -- "$APP_DIR"
if [[ "$PURGE" == true ]]; then
  rm -rf -- "$STATE_DIR"
  if id "$SERVICE_USER" >/dev/null 2>&1; then userdel "$SERVICE_USER" || true; fi
  printf 'Removed Synfull application and state data at %s.\n' "$STATE_DIR"
else
  printf 'Removed Synfull application; persistent data retained at %s.\n' "$STATE_DIR"
  printf 'To remove retained data later, review and remove that directory manually. The service account remains.\n'
fi
