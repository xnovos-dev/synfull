#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${SYNFULL_INSTALL_DIR:-/opt/synfull}"
SERVICE_USER="${SYNFULL_SERVICE_USER:-synfull}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Linux && $EUID -eq 0 ]] || fail 'Run on Linux as root: sudo ./scripts/update-linux.sh'
[[ -x "$APP_DIR/.venv/bin/python" && -d "$APP_DIR/backend" ]] || fail "Synfull installation not found at $APP_DIR."
[[ -f "$SOURCE_DIR/backend/requirements.txt" ]] || fail 'Run from a Synfull source checkout.'
command -v npm >/dev/null && command -v node >/dev/null || fail 'Node.js and npm are required to build frontend assets.'
BACKUP="${SYNFULL_BACKUP_DIR:-$APP_DIR/backups}"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$BACKUP"
STAMP="$(date +%Y%m%d-%H%M%S)"
"$APP_DIR/.venv/bin/python" -c 'import shutil,sys; shutil.copy2(sys.argv[1],sys.argv[2])' \
  "${SYNFULL_DATABASE_PATH:-/var/lib/synfull/synfull.sqlite3}" "$BACKUP/synfull-$STAMP.sqlite3" 2>/dev/null || true
systemctl stop synfull.service
TMP="$(mktemp -d)"
rollback() {
  status=$?
  if [[ $status -ne 0 ]]; then
    printf 'Update failed. Existing state remains untouched; inspect %s and restore the previous release if needed.\n' "$APP_DIR" >&2
    systemctl start synfull.service || true
  fi
  rm -rf "$TMP"
  exit "$status"
}
trap rollback EXIT
# Build into a temporary staging directory before replacing application code.
mkdir -p "$TMP/source"
tar -C "$SOURCE_DIR" --exclude='./.git' --exclude='./.venv' --exclude='./frontend/node_modules' --exclude='./frontend/dist' -cf - . | tar -C "$TMP/source" -xf -
cp -a "$TMP/source/backend/." "$APP_DIR/backend/"
cp -a "$TMP/source/scripts/." "$APP_DIR/scripts/"
cp -a "$TMP/source/frontend/." "$APP_DIR/frontend/"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
if [[ -f "$APP_DIR/frontend/package-lock.json" ]]; then (cd "$APP_DIR/frontend" && npm ci); else (cd "$APP_DIR/frontend" && npm install); fi
(cd "$APP_DIR/frontend" && npm run build)
chown -R root:"$SERVICE_USER" "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
systemctl start synfull.service
systemctl is-active --quiet synfull.service || fail 'Updated service did not become active.'
trap - EXIT
rm -rf "$TMP"
printf 'Synfull updated. Backup (if the SQLite file existed): %s/synfull-%s.sqlite3\n' "$BACKUP" "$STAMP"
