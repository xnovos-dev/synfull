#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${SYNFULL_INSTALL_DIR:-/opt/synfull}"
STATE_DIR="${SYNFULL_STATE_DIR:-/var/lib/synfull}"
SERVICE_USER="${SYNFULL_SERVICE_USER:-synfull}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || fail 'This installer only supports Linux.'
[[ $EUID -eq 0 ]] || fail 'Run with sudo: sudo ./scripts/install-linux.sh'
ARCH="$(uname -m)"
case "$ARCH" in x86_64|aarch64|arm64) ;; *) fail "Unsupported architecture: $ARCH (supported: x86_64, aarch64/arm64)." ;; esac
command -v python3 >/dev/null || fail 'python3 is required; install it with your distribution package manager.'
PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || fail "Python 3.10+ is required (found $PYTHON_VERSION)."
command -v systemctl >/dev/null || fail 'systemd is required for this installer.'
systemctl is-system-running >/dev/null 2>&1 || printf 'WARNING: systemd is present but not fully operational.\n' >&2
if [[ -d "$APP_DIR" ]]; then fail "$APP_DIR already exists. Use the update script or move the existing installation aside first."; fi
if [[ -e "$STATE_DIR" && ! -w "$STATE_DIR" ]]; then fail "State directory exists but is not writable: $STATE_DIR"; fi
[[ -f "$SOURCE_DIR/backend/requirements.txt" && -f "$SOURCE_DIR/frontend/package.json" ]] || fail 'Run this script from a Synfull source checkout.'
if [[ -f "$SOURCE_DIR/frontend/package-lock.json" ]]; then :; elif command -v npm >/dev/null; then printf 'WARNING: no npm lockfile exists; dependency versions may float.\n' >&2; else fail 'Node.js and npm are required to build the frontend, and are not installed.'; fi
command -v node >/dev/null && command -v npm >/dev/null || fail 'Node.js and npm are required for the frontend production build.'
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 18 ]] || fail 'Node.js 18 or newer is required.'

if ! id "$SERVICE_USER" >/dev/null 2>&1; then useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"; fi
install -d -o root -g "$SERVICE_USER" -m 0750 "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$STATE_DIR" "$STATE_DIR/logs"
# Copy only source files; persistent state, local environments and generated dependencies are excluded.
tar -C "$SOURCE_DIR" --exclude='./.git' --exclude='./.venv' --exclude='./frontend/node_modules' --exclude='./frontend/dist' -cf - . | tar -C "$APP_DIR" -xf -
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
if [[ -f "$APP_DIR/frontend/package-lock.json" ]]; then (cd "$APP_DIR/frontend" && npm ci); else (cd "$APP_DIR/frontend" && npm install); fi
(cd "$APP_DIR/frontend" && npm run build)
chown -R root:"$SERVICE_USER" "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
install -m 0644 "$APP_DIR/scripts/synfull.service" /etc/systemd/system/synfull.service
systemctl daemon-reload
systemctl enable --now synfull.service
printf '\nSynfull installed for %s (%s, Python %s).\n' "$ARCH" "$(. /etc/os-release && echo "$PRETTY_NAME")" "$PYTHON_VERSION"
printf 'Dashboard URL: http://127.0.0.1:8000 (LAN listener enabled; configure HTTPS before remote access).\n'
printf 'Service status: systemctl status synfull.service\n'
printf 'State and SQLite database: %s\n' "$STATE_DIR"
printf 'Create the first administrator from this host using %s/initial-admin-token.txt.\n' "$STATE_DIR"
