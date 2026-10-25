from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..collectors.processes import collect_processes
from ..collectors.system import collect_system_metrics, platform_capabilities
from ..config import get_settings
from ..integrations.discord_alerts import public_config as discord_alert_config, send_test_message, update_config as update_discord_alert_config
from ..integrations.status import docker_action, docker_logs, integrations_status
from ..schemas.alerts import DiscordAlertUpdate
from ..schemas.system import ProcessResponse, SystemResponse
from ..schemas.management import AdminSetup, BackupTargetsUpdate, EndpointTargetsUpdate, LoginPayload, PasswordChange, ServiceAllowlistUpdate, ThresholdUpdate, ThemeUpdate, UserCreate, UserUpdate
from ..services import management

router = APIRouter(prefix="/api/v1")


@router.get("/auth/status")
def authentication_status(request: Request):
    return management.auth_status(request.cookies.get(management.SESSION_COOKIE))


@router.post("/auth/setup")
def setup_administrator(payload: AdminSetup, response: Response, request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Initial administrator setup is available only from this host")
    try:
        token = management.create_admin(setup_token=payload.setup_token, username=payload.username, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.set_cookie(management.SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=management.SESSION_SECONDS, path="/")
    return {"authenticated": True, "username": payload.username.strip(), "must_change_password": False}


@router.post("/auth/login")
def authenticate(payload: LoginPayload, response: Response, request: Request):
    try:
        client_ip = request.client.host if request.client else "unknown"
        token = management.login(username=payload.username, password=payload.password, client_ip=client_ip)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.set_cookie(management.SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=management.SESSION_SECONDS, path="/")
    current = management.current_user(payload.username.strip())
    return {"authenticated": True, "username": payload.username.strip(),
            "must_change_password": current["must_change_password"]}


@router.post("/auth/change-password")
def update_own_password(payload: PasswordChange, request: Request, response: Response):
    username = management.get_session_username(request.cookies.get(management.SESSION_COOKIE))
    if not username:
        raise HTTPException(status_code=401, detail="Sign in to change your password")
    try:
        token = management.change_password(username, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.set_cookie(management.SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=management.SESSION_SECONDS, path="/")
    return {"authenticated": True, "username": username, "must_change_password": False}


@router.post("/auth/logout")
def unauthenticate(request: Request, response: Response):
    username = management.get_session_username(request.cookies.get(management.SESSION_COOKIE))
    if username:
        management.logout(request.cookies.get(management.SESSION_COOKIE), username)
    response.delete_cookie(management.SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


@router.get("/auth/me")
def current_administrator(request: Request):
    username = management.require_admin(request)
    return {"authenticated": True, "username": username}


@router.get("/auth/preferences")
def user_preferences(request: Request):
    username = management.require_admin(request)
    return management.current_user(username)


@router.put("/auth/preferences/theme")
@router.post("/auth/preferences/theme")
def set_user_theme(payload: ThemeUpdate, request: Request):
    username = management.require_admin(request)
    return {"theme": management.update_theme(username, payload.theme)}


@router.get("/management/users/permissions")
def available_user_permissions(request: Request):
    management.require_admin(request)
    return {"permissions": management.USER_PERMISSIONS}


@router.get("/management/users")
def list_user_accounts(request: Request):
    username = management.require_admin(request)
    if management.current_user(username)["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can manage user accounts")
    return management.user_accounts()


@router.post("/management/users")
def add_user_account(payload: UserCreate, request: Request):
    username = management.require_admin(request)
    if management.current_user(username)["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can manage user accounts")
    try:
        created = management.create_user(username=payload.username, password=payload.password, role=payload.role,
                                         permissions=payload.permissions, theme=payload.theme)
        management._audit(username=username, action="users.create", target=created["username"], details=f"Created {created['role']} account")
        return created
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/management/users/{user_id}")
def change_user_account(user_id: int, payload: UserUpdate, request: Request):
    username = management.require_admin(request)
    if management.current_user(username)["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can manage user accounts")
    try:
        updated = management.update_user(user_id, role=payload.role, enabled=payload.enabled,
                                         permissions=payload.permissions, password=payload.password, theme=payload.theme)
        management._audit(username=username, action="users.update", target=updated["username"], details="Updated account settings")
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/system", response_model=SystemResponse)
def system(request: Request):
    management.require_admin(request)
    return asdict(collect_system_metrics())


@router.get("/processes", response_model=list[ProcessResponse])
def processes(request: Request):
    management.require_admin(request)
    return collect_processes()


@router.get("/capabilities")
def capabilities(request: Request):
    management.require_admin(request)
    return platform_capabilities()


@router.get("/integrations")
def integrations(request: Request):
    management.require_admin(request)
    return integrations_status()


@router.get("/docker/alerts/discord")
def get_discord_alert_config(request: Request):
    management.require_admin(request)
    return discord_alert_config()


@router.put("/docker/alerts/discord")
def put_discord_alert_config(payload: DiscordAlertUpdate, request: Request):
    management.require_admin(request)
    current = discord_alert_config()
    webhook_url = "" if payload.clear_webhook else (payload.webhook_url or None)
    if payload.enabled and not current["configured"] and webhook_url is None:
        raise HTTPException(status_code=400, detail="Set a Discord webhook URL before enabling alerts")
    if payload.enabled and webhook_url == "":
        raise HTTPException(status_code=400, detail="Set a Discord webhook URL before enabling alerts")
    try:
        result = update_discord_alert_config(enabled=payload.enabled, webhook_url=webhook_url,
                                             events=payload.events,
                                             minimum_interval_seconds=payload.minimum_interval_seconds)
        management._audit(username=management.require_admin(request), action="docker.alerts.discord.update")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/docker/alerts/discord/test")
def test_discord_alert(request: Request):
    management.require_admin(request)
    try:
        send_test_message()
        management._audit(username=management.require_admin(request), action="docker.alerts.discord.test")
        return {"sent": True, "message": "Test alert sent to Discord."}
    except Exception as exc:
        management._audit(username=management.require_admin(request), action="docker.alerts.discord.test", outcome="failed", details=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/docker/containers/{container_id}/logs")
def container_logs(container_id: str, request: Request, tail: int = Query(default=300, ge=1, le=2000)):
    management.require_admin(request)
    try:
        return docker_logs(container_id, tail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/docker/containers/{container_id}/{action}")
def manage_container(container_id: str, action: str, request: Request):
    username = management.require_admin(request)
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="Action must be start, stop, or restart")
    try:
        result = docker_action(container_id, action)
        management._audit(username=username, action=f"docker.{action}", target=result.get("name", container_id))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        management._audit(username=username, action=f"docker.{action}", target=container_id, outcome="failed", details=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/management/security")
def security_summary(request: Request):
    username = management.require_admin(request)
    settings = get_settings()
    return {"username": username, "bind_host": settings.bind_host, "bind_port": settings.bind_port,
            "is_local_only": settings.bind_host in {"127.0.0.1", "localhost", "::1"},
            "transport_is_https": request.url.scheme == "https",
            "session_duration_seconds": management.SESSION_SECONDS,
            "service_allowlist": management.management_settings()["service_allowlist"]}


@router.get("/settings")
def settings_summary(request: Request):
    management.require_admin(request)
    settings = get_settings()
    return {
        "bind_host": settings.bind_host,
        "bind_port": settings.bind_port,
        "state_dir": str(settings.state_dir),
        "database_path": str(settings.resolved_database_path),
        "log_dir": str(settings.resolved_log_dir),
        "collection_interval_seconds": settings.collection_interval_seconds,
        "metric_retention_days": settings.metric_retention_days,
        "development": settings.development,
        "docker_endpoint_configured": bool(settings.docker_endpoint),
        "pm2_executable_configured": bool(settings.pm2_executable),
        "pm2_owner": settings.pm2_owner,
    }


@router.get("/management/services")
def services(request: Request):
    management.require_admin(request)
    configured = set(management.management_settings()["service_allowlist"])
    return [{**service, "allowlisted": service["name"] in configured} for service in management.list_services()]


@router.put("/management/services/allowlist")
def save_service_allowlist(payload: ServiceAllowlistUpdate, request: Request):
    username = management.require_admin(request)
    try:
        return {"services": management.update_service_allowlist(payload.services, username=username)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/management/services/{name}/{action}")
def control_service(name: str, action: str, request: Request):
    username = management.require_admin(request)
    try:
        return management.service_action(name, action, username=username)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/management/jobs")
def jobs(request: Request):
    management.require_admin(request)
    return management.scheduled_jobs()


@router.get("/management/endpoints")
def endpoints(request: Request):
    management.require_admin(request)
    return management.endpoint_results()


@router.put("/management/endpoints")
def save_endpoints(payload: EndpointTargetsUpdate, request: Request):
    username = management.require_admin(request)
    try:
        targets = management.save_endpoint_targets([target.model_dump() for target in payload.targets], username=username)
        return {"targets": targets}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/management/endpoints/check")
def check_endpoints(request: Request):
    username = management.require_admin(request)
    results = []
    targets = management.management_settings()["endpoint_targets"]
    for target in targets:
        try:
            result = management.check_endpoint(target)
        except ValueError as exc:
            result = {**target, "status": "unhealthy", "message": str(exc), "response_code": None,
                      "response_ms": None, "tls_days_remaining": None}
        results.append(result)
    healthy = sum(result["status"] == "healthy" for result in results)
    management._audit(
        username=username,
        action="network.endpoint.check",
        target=f"{len(results)} endpoint(s)",
        outcome="success" if healthy == len(results) else "failed",
        details=f"Checked {len(results)} endpoint(s): {healthy} healthy, {len(results) - healthy} unhealthy.",
    )
    return results


@router.get("/management/backups")
def backups(request: Request):
    management.require_admin(request)
    return management.backup_results()


@router.get("/management/storage/smart")
def smart_storage(request: Request):
    management.require_admin(request)
    return management.smart_health()


@router.put("/management/backups")
def save_backups(payload: BackupTargetsUpdate, request: Request):
    username = management.require_admin(request)
    try:
        targets = management.save_backup_targets([target.model_dump() for target in payload.targets], username=username)
        return {"targets": targets}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/management/metrics/history")
def metric_history(request: Request, hours: int = Query(default=24, ge=1, le=720)):
    management.require_admin(request)
    return management.metric_history(hours=hours)


@router.get("/management/metrics/alerts")
def metric_alert_state(request: Request):
    management.require_admin(request)
    return {"thresholds": management.management_settings()["thresholds"], "alerts": management.metric_alerts()}


@router.put("/management/metrics/thresholds")
def save_metric_thresholds(payload: ThresholdUpdate, request: Request):
    username = management.require_admin(request)
    try:
        return {"thresholds": management.save_thresholds(payload.thresholds, username=username)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/management/audit")
def audit_log(request: Request, limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    management.require_admin(request)
    return management.audit_entries(limit=limit, offset=offset)


@router.get("/management/logs")
def logs(request: Request, query: str = Query(default="", max_length=200), limit: int = Query(default=200, ge=1, le=500)):
    management.require_admin(request)
    return management.system_logs(query=query, limit=limit)
