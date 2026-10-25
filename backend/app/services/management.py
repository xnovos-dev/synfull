from __future__ import annotations

import hashlib
import http.client
import hmac
import ipaddress
import json
import logging
import os
import threading
import re
import secrets
import shutil
import ssl
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import Column, Float, Integer, String, Text, func, inspect, select

from ..config import get_settings
from ..database import Base, SessionLocal, engine

logger = logging.getLogger(__name__)
COMMAND_TIMEOUT_SECONDS = 8
SESSION_COOKIE = "synfull_session"
SESSION_SECONDS = 12 * 60 * 60
SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
_login_lock = threading.Lock()
_login_failures: dict[str, list[float]] = {}


class AdminAccount(Base):
    __tablename__ = "admin_account"
    id = Column(Integer, primary_key=True)
    username = Column(String(80), nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    password_salt = Column(String(64), nullable=False)
    created_at = Column(Float, nullable=False, default=time.time)
    role = Column(String(16), nullable=False, default="admin")
    enabled = Column(Integer, nullable=False, default=1)
    permissions_json = Column(Text, nullable=False, default='["*"]')
    theme = Column(String(24), nullable=False, default="synfull")
    must_change_password = Column(Integer, nullable=False, default=0)


class AdminSession(Base):
    __tablename__ = "admin_session"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, default=1, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(Float, nullable=False, index=True)


class AuditEntry(Base):
    __tablename__ = "audit_entry"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False, index=True, default=time.time)
    username = Column(String(80), nullable=False)
    action = Column(String(120), nullable=False)
    target = Column(String(255), nullable=False, default="")
    outcome = Column(String(24), nullable=False, default="success")
    details = Column(Text, nullable=False, default="")


class ManagementConfig(Base):
    __tablename__ = "management_config"
    id = Column(Integer, primary_key=True, default=1)
    service_allowlist_json = Column(Text, nullable=False, default="[]")
    endpoint_targets_json = Column(Text, nullable=False, default="[]")
    backup_targets_json = Column(Text, nullable=False, default="[]")
    thresholds_json = Column(Text, nullable=False, default='{"cpu_percent":90,"memory_percent":90,"disk_percent":90}')


class MetricSample(Base):
    __tablename__ = "metric_sample"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False, index=True)
    cpu_percent = Column(Float, nullable=True)
    memory_percent = Column(Float, nullable=True)
    disk_percent = Column(Float, nullable=True)


class TargetCheck(Base):
    __tablename__ = "target_check"
    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(String(64), nullable=False, index=True)
    checked_at = Column(Float, nullable=False, index=True)
    status = Column(String(24), nullable=False)
    response_code = Column(Integer, nullable=True)
    response_ms = Column(Float, nullable=True)
    tls_days_remaining = Column(Integer, nullable=True)
    message = Column(Text, nullable=False, default="")


class MetricAlert(Base):
    __tablename__ = "metric_alert"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(48), nullable=False, unique=True)
    active = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=False)
    value = Column(Float, nullable=True)
    threshold = Column(Float, nullable=True)
    changed_at = Column(Float, nullable=False)


Base.metadata.create_all(bind=engine)


def _migrate_account_schema() -> None:
    account_columns = {column["name"] for column in inspect(engine).get_columns("admin_account")}
    session_columns = {column["name"] for column in inspect(engine).get_columns("admin_session")}
    with engine.begin() as connection:
        if "role" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE admin_account ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'admin'")
        if "enabled" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE admin_account ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        if "permissions_json" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE admin_account ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[\"*\"]'")
        if "theme" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE admin_account ADD COLUMN theme VARCHAR(24) NOT NULL DEFAULT 'synfull'")
        if "must_change_password" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE admin_account ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        if "account_id" not in session_columns:
            connection.exec_driver_sql("ALTER TABLE admin_session ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1")


_migrate_account_schema()

USER_PERMISSIONS = {
    "view_dashboard": "View dashboard and live metrics",
    "view_processes": "View process inventory",
    "view_system_info": "View system information and settings summary",
    "view_history": "View metric history and current alert state",
    "view_services": "View host services",
    "manage_services": "Start, stop, or restart allowlisted services",
    "view_jobs": "View scheduled jobs",
    "view_network": "View configured network checks",
    "manage_network": "Add, edit, remove, and run network checks",
    "view_storage": "View storage and backup health",
    "manage_storage": "Change configured backup checks",
    "view_security": "View security posture and service allowlist",
    "manage_security": "Change the service action allowlist",
    "view_audit": "View administrator activity",
    "view_logs": "View host event records",
    "view_alerts": "View Discord alert configuration",
    "manage_alerts": "Change alert configuration and thresholds",
    "view_docker": "View Docker containers and logs",
    "manage_docker": "Start, stop, or restart Docker containers",
}


def _admin_exists() -> bool:
    with SessionLocal() as session:
        return session.scalar(select(AdminAccount.id).where(AdminAccount.role == "admin", AdminAccount.enabled == 1)) is not None


def ensure_default_admin() -> bool:
    """Create the local-only npm distribution's forced-rotation bootstrap account."""
    try:
        with SessionLocal() as session:
            if session.scalar(select(AdminAccount.id).limit(1)) is not None:
                return False
            salt = secrets.token_bytes(32)
            session.add(AdminAccount(
                username="admin", password_hash=_password_hash("admin", salt).hex(), password_salt=salt.hex(),
                role="admin", enabled=1, permissions_json='["*"]', must_change_password=1,
            ))
            session.commit()
    except Exception:
        with SessionLocal() as session:
            if session.scalar(select(AdminAccount.id).limit(1)) is not None:
                return False
        raise
    _audit(username="admin", action="admin.bootstrap", details="Created local default account; password rotation required")
    return True


def setup_token_path() -> Path:
    return get_settings().state_dir / "initial-admin-token.txt"


def ensure_setup_token() -> None:
    if _admin_exists():
        return
    path = setup_token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                token_file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    except OSError:
        logger.exception("Could not create the initial administrator setup token file")


def auth_status(session_token: str | None = None) -> dict[str, Any]:
    if os.environ.get("SYNFULL_BOOTSTRAP_DEFAULT_ADMIN") == "1":
        ensure_default_admin()
    else:
        ensure_setup_token()
    setup_required = not _admin_exists()
    username = get_session_username(session_token)
    settings = get_settings()
    instruction = ("Use SYNFULL_INITIAL_ADMIN_TOKEN from the server environment." if settings.initial_admin_token
                   else "Read initial-admin-token.txt from the Synfull state directory.")
    user = current_user(username) if username else None
    with SessionLocal() as session:
        bootstrap = session.scalar(select(AdminAccount.must_change_password).where(AdminAccount.username == "admin", AdminAccount.enabled == 1))
    return {"setup_required": setup_required, "authenticated": username is not None,
            "username": username, "role": user["role"] if user else None,
            "permissions": user["permissions"] if user else [], "theme": user["theme"] if user else "synfull",
            "must_change_password": bool(user and user["must_change_password"]),
            "default_login_available": bool(bootstrap),
            "setup_instructions": instruction if setup_required else None}


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)


def _audit(*, username: str, action: str, target: str = "", outcome: str = "success", details: str = "") -> None:
    with SessionLocal() as session:
        session.add(AuditEntry(username=username, action=action, target=target[:255], outcome=outcome,
                               details=details[:1000], timestamp=time.time()))
        session.commit()


def create_admin(*, setup_token: str, username: str, password: str) -> str:
    ensure_setup_token()
    settings = get_settings()
    configured_token = settings.initial_admin_token
    token_path = setup_token_path()
    try:
        file_token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        file_token = ""
    if _admin_exists():
        raise ValueError("Administrator setup has already been completed")
    valid_token = bool(setup_token) and (hmac.compare_digest(setup_token, configured_token) if configured_token else hmac.compare_digest(setup_token, file_token))
    if not valid_token:
        _audit(username="anonymous", action="admin.setup", outcome="denied", details="Invalid setup token")
        raise ValueError("Invalid setup token. Read the token from the Synfull state directory or configure SYNFULL_INITIAL_ADMIN_TOKEN.")
    normalized_username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", normalized_username):
        raise ValueError("Username must be 1–80 letters, numbers, dots, underscores, @ signs, or hyphens")
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Password must be between 12 and 256 characters")
    salt = secrets.token_bytes(32)
    account = AdminAccount(username=normalized_username,
                           password_hash=_password_hash(password, salt).hex(), password_salt=salt.hex(),
                           role="admin", enabled=1, permissions_json='["*"]')
    try:
        with SessionLocal() as session:
            session.add(account)
            session.commit()
    except Exception as exc:
        with SessionLocal() as session:
            session.rollback()
            if session.scalar(select(AdminAccount.id).where(AdminAccount.username == normalized_username)) is not None:
                raise ValueError("Administrator setup has already been completed") from exc
        raise
    try:
        token_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Administrator setup completed but setup token file could not be removed")
    _audit(username=normalized_username, action="admin.setup", details="Initial administrator created")
    return issue_session(normalized_username)


def login(*, username: str, password: str, client_ip: str = "unknown") -> str:
    now = time.monotonic()
    with _login_lock:
        failures = [when for when in _login_failures.get(client_ip, []) if now - when < 900]
        if len(failures) >= 10:
            raise PermissionError("Too many unsuccessful sign-in attempts. Try again in 15 minutes.")
        _login_failures[client_ip] = failures
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(func.lower(AdminAccount.username) == username.strip().lower(), AdminAccount.enabled == 1))
        valid = False
        if account is not None:
            try:
                expected = bytes.fromhex(account.password_hash)
                salt = bytes.fromhex(account.password_salt)
                valid = account.username.casefold() == username.strip().casefold() and hmac.compare_digest(_password_hash(password, salt), expected)
            except (ValueError, TypeError):
                valid = False
        if not valid:
            with _login_lock:
                _login_failures.setdefault(client_ip, []).append(now)
            _audit(username="anonymous", action="admin.login", outcome="denied", details="Invalid credentials")
            raise ValueError("Invalid username or password")
        with _login_lock:
            _login_failures.pop(client_ip, None)
        _audit(username=account.username, action="admin.login", target=account.username)
        return issue_session(account.username)


def issue_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username))
        if account is None or not account.enabled:
            raise ValueError("Cannot create a session for a disabled or missing account")
        session.add(AdminSession(account_id=account.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=time.time() + SESSION_SECONDS))
        session.execute(AdminSession.__table__.delete().where(AdminSession.expires_at < time.time()))
        session.commit()
    return token


def get_session_username(token: str | None) -> str | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with SessionLocal() as session:
        row = session.execute(select(AdminAccount.username)
                              .join(AdminSession, AdminSession.account_id == AdminAccount.id)
                              .where(AdminSession.token_hash == digest, AdminSession.expires_at > time.time(),
                                     AdminAccount.enabled == 1)).first()
        return row[0] if row else None


def logout(token: str | None, username: str) -> None:
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with SessionLocal() as session:
            session.execute(AdminSession.__table__.delete().where(AdminSession.token_hash == digest))
            session.commit()
    _audit(username=username, action="admin.logout")


def _permission_for_request(request) -> str | None:
    path = request.url.path.removeprefix("/api/v1/")
    method = request.method.upper()
    if path == "system": return "view_dashboard"
    if path == "processes": return "view_processes"
    if path == "settings": return "view_system_info"
    if path == "auth/preferences": return None
    if path == "auth/preferences/theme": return None
    if path == "integrations": return "view_docker"
    if path == "capabilities": return "view_dashboard"
    if path == "docker/alerts/discord": return "manage_alerts" if method in {"PUT", "POST"} else "view_alerts"
    if path == "docker/alerts/discord/test": return "manage_alerts"
    if path.startswith("docker/containers/"):
        return "manage_docker" if method == "POST" else "view_docker"
    if path.startswith("management/users"): return "view_security"
    if path == "management/security": return "view_security"
    if path == "management/services": return "view_services"
    if path == "management/services/allowlist": return "manage_security"
    if path.startswith("management/services/"): return "manage_services"
    if path == "management/jobs": return "view_jobs"
    if path == "management/endpoints": return "manage_network" if method == "PUT" else "view_network"
    if path == "management/endpoints/check": return "manage_network"
    if path in {"management/backups", "management/storage/smart"}:
        return "manage_storage" if path.endswith("backups") and method == "PUT" else "view_storage"
    if path == "management/metrics/history": return "view_history"
    if path == "management/metrics/alerts": return "manage_alerts" if method == "PUT" else "view_history"
    if path == "management/metrics/thresholds": return "manage_alerts"
    if path == "management/audit": return "view_audit"
    if path == "management/logs": return "view_logs"
    return None


def require_admin(request) -> str:
    from fastapi import HTTPException
    username = get_session_username(request.cookies.get(SESSION_COOKIE))
    if username is None:
        raise HTTPException(status_code=401, detail="Sign in to access this resource")
    required = _permission_for_request(request)
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username))
        if account is None or not account.enabled:
            raise HTTPException(status_code=401, detail="Account is disabled")
        if account.must_change_password and request.url.path.removeprefix("/api/v1/") != "auth/change-password":
            raise HTTPException(status_code=403, detail="Change the default password before using Synfull")
        if account.role == "admin":
            return username
        try:
            permissions = json.loads(account.permissions_json)
        except (TypeError, json.JSONDecodeError):
            permissions = []
        if required is None and request.url.path.removeprefix("/api/v1/") not in {"auth/preferences", "auth/preferences/theme"}:
            raise HTTPException(status_code=403, detail="Your account does not have permission to access this resource")
        if required is not None and required not in permissions:
            raise HTTPException(status_code=403, detail="Your account does not have permission to access this resource")
    return username


def user_accounts() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        accounts = session.scalars(select(AdminAccount).order_by(AdminAccount.username)).all()
        return [{"id": account.id, "username": account.username, "role": account.role,
                 "enabled": bool(account.enabled), "permissions": json.loads(account.permissions_json),
                 "theme": account.theme, "created_at": account.created_at} for account in accounts]


def create_user(*, username: str, password: str, role: str, permissions: list[str], theme: str = "synfull") -> dict[str, Any]:
    normalized = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", normalized):
        raise ValueError("Username must be 1–80 letters, numbers, dots, underscores, @ signs, or hyphens")
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Password must be between 12 and 256 characters")
    if role not in {"admin", "guest"}:
        raise ValueError("Role must be admin or guest")
    if theme not in {"synfull", "monochrome", "hacker", "light", "oled"}:
        raise ValueError("Choose a supported dashboard theme")
    if role == "guest" and (not set(permissions) <= USER_PERMISSIONS.keys() or len(set(permissions)) != len(permissions)):
        raise ValueError("Choose only supported unique permissions")
    salt = secrets.token_bytes(32)
    account = AdminAccount(username=normalized, password_hash=_password_hash(password, salt).hex(),
                           password_salt=salt.hex(), role=role, enabled=1, theme=theme,
                           permissions_json=json.dumps(["*"] if role == "admin" else sorted(set(permissions))))
    with SessionLocal() as session:
        session.add(account)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise ValueError("An account with that username already exists") from exc
        session.refresh(account)
        created = {"id": account.id, "username": account.username, "role": account.role,
                   "enabled": True, "permissions": json.loads(account.permissions_json),
                   "theme": account.theme, "created_at": account.created_at}
    return created


def update_user(user_id: int, *, role: str, enabled: bool, permissions: list[str], password: str | None = None, theme: str | None = None) -> dict[str, Any]:
    if role not in {"admin", "guest"}:
        raise ValueError("Role must be admin or guest")
    if role == "guest" and (not set(permissions) <= USER_PERMISSIONS.keys() or len(set(permissions)) != len(permissions)):
        raise ValueError("Choose only supported unique permissions")
    if password is not None and not 12 <= len(password) <= 256:
        raise ValueError("Password must be between 12 and 256 characters")
    if theme is not None and theme not in {"synfull", "monochrome", "hacker", "light", "oled"}:
        raise ValueError("Choose a supported dashboard theme")
    with SessionLocal() as session:
        account = session.get(AdminAccount, user_id)
        if account is None:
            raise ValueError("Account not found")
        if account.role == "admin" and (role != "admin" or not enabled):
            remaining_admins = session.scalar(select(func.count()).select_from(AdminAccount).where(
                AdminAccount.role == "admin", AdminAccount.enabled == 1, AdminAccount.id != user_id)) or 0
            if remaining_admins == 0:
                raise ValueError("At least one enabled administrator account must remain")
        if account.role == "admin" and role == "admin" and enabled:
            if password is None and permissions and permissions != ["*"]:
                raise ValueError("Administrator permissions are full access and cannot be restricted")
        account.role = role
        account.enabled = int(enabled)
        account.permissions_json = json.dumps(["*"] if role == "admin" else sorted(set(permissions)))
        if theme is not None:
            account.theme = theme
        if password is not None:
            salt = secrets.token_bytes(32)
            account.password_salt = salt.hex()
            account.password_hash = _password_hash(password, salt).hex()
            account.must_change_password = 0
        if not enabled:
            session.execute(AdminSession.__table__.delete().where(AdminSession.account_id == user_id))
        session.commit()
        return {"id": account.id, "username": account.username, "role": account.role,
                "enabled": bool(account.enabled), "permissions": json.loads(account.permissions_json),
                "theme": account.theme, "created_at": account.created_at}


def current_user(username: str) -> dict[str, Any]:
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username))
        if account is None:
            raise ValueError("Account not found")
        return {"id": account.id, "username": account.username, "role": account.role,
                "enabled": bool(account.enabled), "permissions": json.loads(account.permissions_json),
                "theme": account.theme, "must_change_password": bool(account.must_change_password)}


def change_password(username: str, password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must be between 12 and 256 characters")
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username, AdminAccount.enabled == 1))
        if account is None:
            raise ValueError("Account not found or disabled")
        if not account.must_change_password:
            raise ValueError("This password-change flow is only available for the required first-login change")
        salt = secrets.token_bytes(32)
        account.password_salt = salt.hex()
        account.password_hash = _password_hash(password, salt).hex()
        account.must_change_password = 0
        session.execute(AdminSession.__table__.delete().where(AdminSession.account_id == account.id))
        session.commit()
    _audit(username=username, action="admin.password.change", target=username, details="Password changed")
    return issue_session(username)


def update_theme(username: str, theme: str) -> str:
    if theme not in {"synfull", "monochrome", "hacker", "light", "oled"}:
        raise ValueError("Choose a supported dashboard theme")
    with SessionLocal() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username))
        if account is None:
            raise ValueError("Account not found")
        account.theme = theme
        session.commit()
    return theme


def audit_entries(*, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(AuditEntry).order_by(AuditEntry.timestamp.desc(), AuditEntry.id.desc()).offset(offset).limit(limit)).all()
        return [{"id": row.id, "timestamp": row.timestamp, "username": row.username, "action": row.action,
                 "target": row.target, "outcome": row.outcome, "details": row.details} for row in rows]


def _config(session) -> ManagementConfig:
    config = session.get(ManagementConfig, 1)
    if config is None:
        config = ManagementConfig(id=1)
        session.add(config)
        session.flush()
    return config


def _json_list(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def management_settings() -> dict[str, Any]:
    with SessionLocal() as session:
        config = _config(session)
        session.commit()
        return {"service_allowlist": json.loads(config.service_allowlist_json),
                "endpoint_targets": _json_list(config.endpoint_targets_json),
                "backup_targets": _json_list(config.backup_targets_json),
                "thresholds": json.loads(config.thresholds_json)}


def update_service_allowlist(names: list[str], *, username: str) -> list[str]:
    normalized = sorted(set(name.strip() for name in names if name.strip()))
    if len(normalized) > 250 or any(not SERVICE_NAME_PATTERN.fullmatch(name) for name in normalized):
        raise ValueError("Service names must use only letters, numbers, dots, underscores, @ signs, colons, or hyphens")
    with SessionLocal() as session:
        config = _config(session)
        config.service_allowlist_json = json.dumps(normalized)
        session.commit()
    _audit(username=username, action="security.service_allowlist.update", details=f"Configured {len(normalized)} service(s)")
    return normalized


def list_services() -> list[dict[str, Any]]:
    if sys.platform == "win32":
        command = shutil.which("powershell.exe") or shutil.which("powershell")
        if not command:
            return []
        script = "Get-Service | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress"
        args = [command, "-NoProfile", "-NonInteractive", "-Command", script]
        code, stdout, _ = _run(args)
        if code:
            return []
        try:
            rows = json.loads(stdout)
            if isinstance(rows, dict):
                rows = [rows]
            return [{"name": row.get("Name", ""), "display_name": row.get("DisplayName", ""),
                     "status": str(row.get("Status", "unknown")).lower(), "startup_type": str(row.get("StartType", "unknown")).lower()}
                    for row in rows if row.get("Name")]
        except (json.JSONDecodeError, AttributeError):
            return []
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return []
    code, stdout, _ = _run([systemctl, "list-units", "--type=service", "--all", "--no-legend", "--plain"])
    services = []
    if code:
        return services
    for line in stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            services.append({"name": parts[0], "display_name": parts[4] if len(parts) > 4 else parts[0],
                             "status": parts[3].lower(), "startup_type": "unknown"})
    return services


def _run(args: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
                                check=False, shell=False)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def service_action(name: str, action: str, *, username: str) -> dict[str, str]:
    if action not in {"start", "stop", "restart"} or not SERVICE_NAME_PATTERN.fullmatch(name):
        raise ValueError("Invalid service or action")
    allowed = management_settings()["service_allowlist"]
    if name not in allowed:
        _audit(username=username, action=f"service.{action}", target=name, outcome="denied", details="Service is not allowlisted")
        raise PermissionError("This service is not in the configured action allowlist")
    if sys.platform == "win32":
        executable = shutil.which("sc.exe") or shutil.which("sc")
        if not executable:
            raise RuntimeError("Windows Service Control is unavailable")
        commands = [[executable, "stop", name], [executable, "start", name]] if action == "restart" else [[executable, action, name]]
    else:
        executable = shutil.which("systemctl")
        if not executable:
            raise RuntimeError("systemd is unavailable")
        commands = [[executable, action, name]]
    for command in commands:
        code, _, stderr = _run(command)
        if code:
            _audit(username=username, action=f"service.{action}", target=name, outcome="failed", details=stderr[:500])
            raise RuntimeError(stderr.strip() or f"Service {action} command failed")
    _audit(username=username, action=f"service.{action}", target=name)
    return {"name": name, "action": action, "status": "requested"}


def save_endpoint_targets(targets: list[dict[str, Any]], *, username: str) -> list[dict[str, Any]]:
    if len(targets) > 100:
        raise ValueError("Configure no more than 100 endpoint checks")
    normalized = []
    for target in targets:
        name = str(target.get("name", "")).strip()[:100]
        url = str(target.get("url", "")).strip()
        parsed = urlparse(url)
        if not name or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Each endpoint needs a name and an HTTP or HTTPS URL without embedded credentials or fragments")
        if len(url) > 2048:
            raise ValueError("Endpoint URL exceeds the 2048 character limit")
        _validate_endpoint_host(parsed)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Endpoint URL has an invalid port") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Endpoint URL has an invalid port")
        target_id = hashlib.sha256(f"{name.casefold()}|{url}".encode()).hexdigest()[:24]
        normalized.append({"id": target_id, "name": name, "url": url})
    with SessionLocal() as session:
        config = _config(session)
        config.endpoint_targets_json = json.dumps(normalized)
        session.commit()
    _audit(username=username, action="network.targets.update", details=f"Configured {len(normalized)} endpoint(s)")
    return normalized


def save_backup_targets(targets: list[dict[str, Any]], *, username: str) -> list[dict[str, Any]]:
    if len(targets) > 100:
        raise ValueError("Configure no more than 100 backup paths")
    normalized = []
    for target in targets:
        name = str(target.get("name", "")).strip()[:100]
        raw_path = str(target.get("path", "")).strip()
        if not name or not raw_path or len(raw_path) > 1024:
            raise ValueError("Each backup check needs a name and a local path")
        path = Path(raw_path).expanduser().resolve()
        max_age_hours = int(target.get("max_age_hours", 48))
        if not 1 <= max_age_hours <= 8760:
            raise ValueError("Backup freshness limit must be between 1 and 8760 hours")
        target_id = hashlib.sha256(f"{name.casefold()}|{path}".encode()).hexdigest()[:24]
        normalized.append({"id": target_id, "name": name, "path": str(path), "max_age_hours": max_age_hours})
    with SessionLocal() as session:
        config = _config(session)
        config.backup_targets_json = json.dumps(normalized)
        session.commit()
    _audit(username=username, action="storage.backup_targets.update", details=f"Configured {len(normalized)} backup path(s)")
    return normalized


def save_thresholds(thresholds: dict[str, float], *, username: str) -> dict[str, float]:
    keys = {"cpu_percent", "memory_percent", "disk_percent"}
    if set(thresholds) != keys or any(not isinstance(v, (float, int)) or not 1 <= v <= 100 for v in thresholds.values()):
        raise ValueError("Set CPU, memory, and disk thresholds between 1 and 100 percent")
    normalized = {key: float(value) for key, value in thresholds.items()}
    with SessionLocal() as session:
        config = _config(session)
        config.thresholds_json = json.dumps(normalized)
        session.commit()
    _audit(username=username, action="monitoring.thresholds.update", details="Metric thresholds updated")
    return normalized


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _record_target_check(*, target_id: str, status: str, code: int | None, response_ms: float | None,
                         tls_days: int | None, message: str) -> dict[str, Any]:
    checked_at = time.time()
    with SessionLocal() as session:
        session.add(TargetCheck(target_id=target_id, checked_at=checked_at, status=status, response_code=code,
                                response_ms=response_ms, tls_days_remaining=tls_days, message=message[:500]))
        session.commit()
    return {"checked_at": checked_at, "status": status, "response_code": code,
            "response_ms": response_ms, "tls_days_remaining": tls_days, "message": message[:500]}


def _validate_endpoint_host(parsed) -> None:
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("Endpoint URL must use HTTP or HTTPS and include a hostname")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Endpoint URL cannot include embedded credentials or fragments")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        resolved_addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Endpoint host could not be resolved: {exc}") from exc
    if not resolved_addresses or any(not ipaddress.ip_address(address).is_global for address in resolved_addresses):
        raise ValueError("Endpoint must resolve only to public addresses; private and local network targets are blocked")


def check_endpoint(target: dict[str, Any]) -> dict[str, Any]:
    url = target["url"]
    parsed = urlparse(url)
    if len(url) > 2048:
        raise ValueError("Endpoint URL exceeds the 2048 character limit")
    _validate_endpoint_host(parsed)
    started = time.monotonic()
    tls_days = None
    try:
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            address = next(address for address in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
                           if ipaddress.ip_address(address[4][0]).is_global)[4][0]
            with socket.create_connection((address, parsed.port or 443), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=parsed.hostname) as secured:
                    if not ipaddress.ip_address(secured.getpeername()[0]).is_global:
                        raise ValueError("Endpoint resolved to a private or local address during the check")
                    cert = secured.getpeercert()
                    expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                    tls_days = (expiry - datetime.now(timezone.utc)).days
        request = Request(url, headers={"User-Agent": "Synfull/0.1 endpoint-health-check"}, method="GET")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            resolved = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"Endpoint host could not be resolved: {exc}") from exc
        addresses = [address[4][0] for address in resolved]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Endpoint resolved to a private or local address during the check")
        # Resolve on configuration and at each check to reject DNS changes to internal addresses.
        try:
            with build_opener(_NoRedirect).open(request, timeout=8) as response:
                code = response.status
                response.read(4096)
        except HTTPError as response:
            code = response.code
            response.close()
        elapsed = round((time.monotonic() - started) * 1000, 1)
        status = "healthy" if 200 <= code < 400 and (tls_days is None or tls_days >= 0) else "unhealthy"
        message = (f"Endpoint redirected with HTTP {code}." if 300 <= code < 400 else
                   "Endpoint responded successfully." if status == "healthy" else f"Endpoint returned HTTP {code}.")
        if tls_days is not None and tls_days > 30:
            message += f" TLS certificate expires in {tls_days} days."
        if tls_days is not None and tls_days <= 30:
            message = f"TLS certificate expires in {tls_days} days."
            if tls_days < 0:
                status = "unhealthy"
        return {**target, **_record_target_check(target_id=target["id"], status=status, code=code,
                                                  response_ms=elapsed, tls_days=tls_days, message=message)}
    except (OSError, URLError, TimeoutError, KeyError, ValueError, ssl.SSLError, http.client.HTTPException) as exc:
        return {**target, **_record_target_check(target_id=target["id"], status="unhealthy", code=None,
                                                  response_ms=round((time.monotonic() - started) * 1000, 1),
                                                  tls_days=tls_days, message=f"Endpoint check failed: {exc}")}


def endpoint_results() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.execute(select(TargetCheck).order_by(TargetCheck.checked_at.desc())).all()
    latest = {}
    for (row,) in rows:
        if row.target_id not in latest:
            latest[row.target_id] = {"checked_at": row.checked_at, "status": row.status,
                                     "response_code": row.response_code, "response_ms": row.response_ms,
                                     "tls_days_remaining": row.tls_days_remaining, "message": row.message}
    return [{**target, **latest.get(target["id"], {"status": "pending", "message": "Waiting for first check"})}
            for target in management_settings()["endpoint_targets"]]


def smart_health() -> list[dict[str, str]]:
    executable = shutil.which("smartctl")
    if not executable:
        return [{"device": "SMART diagnostics", "status": "unsupported", "message": "Install smartmontools and ensure smartctl is available to enable disk-health checks."}]
    code, stdout, stderr = _run([executable, "--scan-open"])
    if code and not stdout.strip():
        return [{"device": "SMART diagnostics", "status": "unavailable", "message": stderr.strip() or "Unable to enumerate SMART-capable disks."}]
    devices = []
    for line in stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            device = line.split()[0]
            if device.startswith("/dev/") or (sys.platform == "win32" and device.lower().startswith("\\\\.\\physicaldrive")):
                devices.append(device)
    results = []
    for device in devices[:32]:
        code, output, error = _run([executable, "-H", device])
        text = output + "\
" + error
        passed = "PASSED" in text.upper() or "HEALTH STATUS: OK" in text.upper()
        failed = "FAILED" in text.upper() or "HEALTH STATUS: BAD" in text.upper()
        results.append({"device": device, "status": "healthy" if passed else "unhealthy" if failed or code & 0x7C else "unknown",
                        "message": next((line.strip() for line in text.splitlines() if "health" in line.lower() or "passed" in line.lower() or "failed" in line.lower()), text.strip()[:300] or "No SMART health result returned.")})
    return results or [{"device": "SMART diagnostics", "status": "unsupported", "message": "No SMART-capable devices were detected."}]


def backup_results() -> list[dict[str, Any]]:
    results = []
    now = time.time()
    for target in management_settings()["backup_targets"]:
        path = Path(target["path"])
        try:
            if path.is_dir():
                candidates = (entry for entry in path.iterdir() if entry.is_file())
                newest = max(candidates, key=lambda entry: entry.stat().st_mtime, default=None)
            elif path.is_file():
                newest = path
            else:
                newest = None
            mtime = newest.stat().st_mtime if newest else None
            age_hours = round((now - mtime) / 3600, 1) if mtime is not None else None
            status = "healthy" if age_hours is not None and age_hours <= target["max_age_hours"] else "stale"
            message = f"Latest file is {age_hours} hours old." if age_hours is not None else "No backup file found at this path."
        except OSError as exc:
            mtime, age_hours, status, message = None, None, "unavailable", str(exc)
        results.append({**target, "status": status, "latest_modified": mtime, "age_hours": age_hours, "message": message})
    return results


def scheduled_jobs() -> list[dict[str, str]]:
    jobs = []
    if sys.platform == "win32":
        executable = shutil.which("schtasks.exe") or shutil.which("schtasks")
        if not executable:
            return jobs
        code, stdout, _ = _run([executable, "/Query", "/FO", "CSV", "/V"])
        if code:
            return jobs
        try:
            import csv
            rows = csv.DictReader(stdout.splitlines())
            for row in rows:
                jobs.append({"name": row.get("TaskName", ""), "schedule": row.get("Schedule Type", ""),
                             "last_run": row.get("Last Run Time", ""), "next_run": row.get("Next Run Time", ""),
                             "last_result": row.get("Last Result", ""), "status": row.get("Status", "unknown")})
        except (csv.Error, AttributeError):
            return []
        return jobs
    systemctl = shutil.which("systemctl")
    if systemctl:
        code, stdout, _ = _run([systemctl, "list-timers", "--all", "--no-legend", "--no-pager"])
        if code == 0:
            for line in stdout.splitlines():
                parts = line.split(None, 5)
                if len(parts) >= 5:
                    timer_name = next((part for part in reversed(parts) if part.endswith((".timer", ".service"))), parts[-1])
                    jobs.append({"name": timer_name, "schedule": "systemd timer", "last_run": parts[0],
                                 "next_run": parts[1], "last_result": "", "status": "scheduled"})
    cron = shutil.which("crontab")
    if cron:
        code, stdout, _ = _run([cron, "-l"])
        if code == 0:
            for index, line in enumerate(stdout.splitlines(), start=1):
                if line.strip() and not line.lstrip().startswith("#"):
                    jobs.append({"name": f"user crontab line {index}", "schedule": line[:500], "last_run": "",
                                 "next_run": "", "last_result": "", "status": "configured"})
    return jobs


def system_logs(*, query: str = "", limit: int = 200) -> list[dict[str, str]]:
    limit = max(1, min(limit, 500))
    query = query.casefold().strip()[:200]
    if sys.platform == "win32":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return []
        script = f"Get-WinEvent -LogName System -MaxEvents {limit} -ErrorAction SilentlyContinue | Select-Object TimeCreated,LevelDisplayName,ProviderName,Id,Message | ConvertTo-Json -Compress"
        code, stdout, _ = _run([powershell, "-NoProfile", "-NonInteractive", "-Command", script])
        if code:
            return []
        try:
            rows = json.loads(stdout)
            if isinstance(rows, dict):
                rows = [rows]
            values = [{"timestamp": str(row.get("TimeCreated", "")), "level": str(row.get("LevelDisplayName", "")),
                       "source": str(row.get("ProviderName", "")), "message": str(row.get("Message", ""))} for row in rows]
        except (json.JSONDecodeError, AttributeError):
            return []
    else:
        journalctl = shutil.which("journalctl")
        if not journalctl:
            return []
        code, stdout, _ = _run([journalctl, "--no-pager", "-n", str(limit), "-o", "json"])
        if code:
            return []
        if len(stdout) > 5_000_000:
            logger.warning("Refusing to parse an oversized journal response")
            return []
        values = []
        for line in stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = row.get("__REALTIME_TIMESTAMP", "")
            try:
                timestamp = datetime.fromtimestamp(int(timestamp) / 1_000_000, timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                timestamp = str(timestamp)
            values.append({"timestamp": timestamp, "level": row.get("PRIORITY", ""),
                           "source": row.get("SYSLOG_IDENTIFIER", ""), "message": str(row.get("MESSAGE", ""))})
    return [row for row in values if not query or query in " ".join(row.values()).casefold()]


def metric_history(*, hours: int = 24) -> list[dict[str, Any]]:
    hours = max(1, min(hours, 720))
    cutoff = time.time() - hours * 3600
    with SessionLocal() as session:
        rows = session.scalars(select(MetricSample).where(MetricSample.timestamp >= cutoff)
                               .order_by(MetricSample.timestamp.asc()).limit(10000)).all()
    return [{"timestamp": row.timestamp, "cpu_percent": row.cpu_percent,
             "memory_percent": row.memory_percent, "disk_percent": row.disk_percent} for row in rows]


def collect_monitoring_sample() -> None:
    from ..collectors.system import collect_system_metrics
    metrics = collect_system_metrics()
    memory = metrics.memory_used / metrics.memory_total * 100 if metrics.memory_total else None
    disk = sum(item["used"] for item in metrics.disks) / sum(item["total"] for item in metrics.disks) * 100 if metrics.disks and sum(item["total"] for item in metrics.disks) else None
    thresholds = management_settings()["thresholds"]
    values = {"cpu_percent": metrics.cpu_percent, "memory_percent": memory, "disk_percent": disk}
    now = time.time()
    notifications = []
    retention = max(1, min(get_settings().metric_retention_days, 3650)) * 86400
    with SessionLocal() as session:
        session.add(MetricSample(timestamp=now, cpu_percent=metrics.cpu_percent, memory_percent=memory, disk_percent=disk))
        session.execute(MetricSample.__table__.delete().where(MetricSample.timestamp < now - retention))
        for key, value in values.items():
            if value is None:
                continue
            active = value >= thresholds[key]
            alert = session.scalar(select(MetricAlert).where(MetricAlert.key == key))
            if alert is None:
                session.add(MetricAlert(key=key, active=int(active), message=f"{key} is {value:.1f}% (threshold {thresholds[key]:.0f}%)",
                                        value=value, threshold=thresholds[key], changed_at=now))
                if active:
                    notifications.append((f"{key.replace('_', ' ').title()} threshold exceeded",
                                         "\n".join((f"⚠️ **Metric:** {key.replace('_', ' ').title()}",
                                                     f"**Current:** {value:.1f}%",
                                                     f"**Threshold:** {thresholds[key]:.0f}%"))))
            elif bool(alert.active) != active or alert.threshold != thresholds[key]:
                was_active = bool(alert.active)
                alert.active = int(active)
                alert.value = value
                alert.threshold = thresholds[key]
                alert.message = f"{key} is {value:.1f}% (threshold {thresholds[key]:.0f}%)"
                alert.changed_at = now
                if active and not was_active:
                    notifications.append((f"{key.replace('_', ' ').title()} threshold exceeded",
                                         "\n".join((f"⚠️ **Metric:** {key.replace('_', ' ').title()}",
                                                     f"**Current:** {value:.1f}%",
                                                     f"**Threshold:** {thresholds[key]:.0f}%"))))
            else:
                alert.value = value
                alert.threshold = thresholds[key]
                alert.message = f"{key} is {value:.1f}% (threshold {thresholds[key]:.0f}%)"
        session.commit()
    for title, message in notifications:
        try:
            from ..integrations.discord_alerts import public_config, send_discord_message
            if public_config()["enabled"]:
                send_discord_message(message, title=title)
        except Exception as exc:
            logger.debug("Metric alert notification unavailable: %s", exc)


def metric_alerts() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(MetricAlert).order_by(MetricAlert.key)).all()
        return [{"key": row.key, "active": bool(row.active), "message": row.message, "value": row.value,
                 "threshold": row.threshold, "changed_at": row.changed_at} for row in rows]


def run_configured_checks() -> None:
    settings = management_settings()
    for target in settings["endpoint_targets"]:
        try:
            result = check_endpoint(target)
        except ValueError as exc:
            logger.warning("Rejected configured endpoint %s: %s", target.get("name", "unknown"), exc)
            continue
        if result["status"] == "unhealthy" or (result.get("tls_days_remaining") is not None and result["tls_days_remaining"] <= 30):
            try:
                from ..integrations.discord_alerts import public_config, send_discord_message
                if public_config()["enabled"]:
                    send_discord_message(
                        "\n".join((
                            f"⚠️ **Endpoint:** {target['name']}",
                            f"**URL:** {target['url']}",
                            "**Status:** Unhealthy",
                            f"**Details:** {result['message']}",
                        )),
                        title="Endpoint health alert",
                    )
            except Exception as exc:
                logger.debug("Endpoint alert notification unavailable: %s", exc)


def monitor_loop(stop_event) -> None:
    last_full_check = 0.0
    while not stop_event.is_set():
        try:
            collect_monitoring_sample()
            if time.monotonic() - last_full_check >= 60:
                run_configured_checks()
                last_full_check = time.monotonic()
        except Exception:
            logger.exception("Background management monitoring iteration failed")
        stop_event.wait(max(5.0, get_settings().collection_interval_seconds))
