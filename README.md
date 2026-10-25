# Synfull

**Local-first host monitoring for Windows and Linux.** Synfull brings live system metrics, process and service visibility, operational checks, and Docker/PM2 integrations together in a self-hosted dashboard.

[![npm version](https://img.shields.io/npm/v/synfull)](https://www.npmjs.com/package/synfull)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## What it does

- Live CPU, memory, storage, network, uptime, temperature, and process monitoring.
- Host service and scheduled-job inventory, system logs, and an administrator audit trail.
- Optional Docker and PM2 integrations, with container controls and Docker event alerts.
- Endpoint availability/TLS checks, backup freshness checks, resource history, and thresholds.
- User accounts, guest permissions, and per-user dashboard themes.
- A local-first web dashboard with an optional, explicit LAN-access choice.

Synfull is built with a **FastAPI/Python backend** and a **React/Vite frontend**, distributed through npm with a small Node.js CLI.

## Install and run

### Requirements

- Node.js 18 or newer and npm.
- Python 3.10 or newer. Synfull creates its own virtual environment on first setup.
- On Ubuntu/Debian, install `python3-venv` (or the matching `python3.X-venv` package) if Python reports that pip/venv support is missing. Some Linux distributions may need build tools when a Python dependency has no prebuilt wheel.

### From npm

```sh
npm install -g synfull
synfull setup
synfull start
synfull open
```

`synfull setup` prepares the Python runtime and dependencies. It asks whether to enable LAN access when run in an interactive terminal; **No/Enter keeps Synfull on `127.0.0.1`**, accessible only from the host. Choosing LAN access binds to `0.0.0.0`; other devices can then connect to `http://<server-lan-ip>:8000`.

The first local login is `admin` / `admin`. Synfull requires a new password of at least 12 characters before allowing access to the dashboard. If you enable LAN access, change this password from the server itself before signing in remotely. Bind only on a trusted LAN, restrict port `8000` with your firewall, and do not expose the service directly to the public internet.

### Commands

| Command | Description |
| --- | --- |
| `synfull setup` | Ask setup options and prepare the isolated Python runtime/dependencies. |
| `synfull start` | Start Synfull in the background. |
| `synfull reload` | Refresh installed application files from the current CLI package and restart the service/background instance. |
| `synfull run` | Run Synfull in the foreground. |
| `synfull status` | Show process status, dashboard address, and data location. |
| `synfull stop` | Stop a background instance. |
| `synfull open` | Open the dashboard on the local machine. |
| `synfull install-service` | Explicitly register automatic startup (Windows sign-in task or Linux systemd service). |
| `synfull uninstall-service` | Remove startup registration; retain application data. |

Service registration is optional and is never performed by npm installation. On Linux, installing or removing the system-wide systemd service requires `sudo`; Synfull runs as the invoking user, not as root. After updating the global npm package, run `synfull reload` to refresh the installed runtime and restart it. Reload does not download or install npm updates itself; use `npm update -g synfull` first.

## Optional integrations and security

### Docker on Linux

During interactive Linux setup, Synfull offers to add your account to the `docker` group so it can access the Docker daemon. This is optional. **Membership in the Docker group grants root-equivalent control of the machine.** Only approve this for an account you trust with administrator-level access.

If the Synfull systemd service is installed, setup restarts it after granting access so it picks up the new group membership. For a manually started instance, log out and back in (or start a new session) before running Synfull again. Docker must be installed and its daemon must be running.

### LAN access

The LAN option is deliberately opt-in. `0.0.0.0` listens on every network interface, not just a private LAN interface. Use a trusted network and firewall rules that allow only the devices/subnet you intend. Authentication is required, but the initial password must be rotated locally before remote login.

## Configuration

The CLI stores its runtime and preferences under the user's local application-data directory. Persistent application state is stored separately.

| Variable | Purpose |
| --- | --- |
| `SYNFULL_HOME` | Override the CLI application/runtime directory. |
| `SYNFULL_STATE_DIR` | Override the directory for persistent database and application state. |
| `SYNFULL_BIND_PORT` | Change the listening port (default `8000`). |
| `SYNFULL_BIND_HOST` | Override the bind host for a command/service install: `127.0.0.1` or `0.0.0.0`. Setup's saved choice applies when this is unset. |

## Development

Clone the repository, then install the backend and frontend development dependencies.

### Windows PowerShell

```powershell
.\scripts\dev.ps1
```

### Linux/macOS shell

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run dev
```

The Vite development server proxies API requests to the backend. In another terminal, start the API from the project root:

```sh
PYTHONPATH=backend python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Build the frontend for production with:

```sh
npm --prefix frontend run build
```

Run checks from the project root (with backend development dependencies installed):

```sh
PYTHONPATH=backend python -m pytest backend/tests
npm --prefix frontend run lint
```

## License

Synfull is released under the [MIT License](LICENSE).
