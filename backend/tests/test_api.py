from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.integrations import discord_alerts
from app.services import management


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite3'}", connect_args={"check_same_thread": False})
    management.Base.metadata.create_all(test_engine)
    discord_alerts.Base.metadata.create_all(test_engine)
    session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(management, "SessionLocal", session_factory)
    monkeypatch.setattr(discord_alerts, "SessionLocal", session_factory)
    monkeypatch.setattr(management, "setup_token_path", lambda: tmp_path / "initial-admin-token.txt")
    (tmp_path / "initial-admin-token.txt").write_text("test-only-setup-token-for-this-client", encoding="utf-8")
    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/auth/setup", json={
            "setup_token": "test-only-setup-token-for-this-client",
            "username": "test-admin",
            "password": "test-admin-password-long",
        })
        assert response.status_code == 200, response.text
        yield test_client
    test_engine.dispose()


def test_default_admin_bootstrap_and_required_password_change(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.sqlite3'}", connect_args={"check_same_thread": False})
    management.Base.metadata.create_all(test_engine)
    discord_alerts.Base.metadata.create_all(test_engine)
    session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(management, "SessionLocal", session_factory)
    monkeypatch.setattr(discord_alerts, "SessionLocal", session_factory)
    monkeypatch.setenv("SYNFULL_BOOTSTRAP_DEFAULT_ADMIN", "1")
    with TestClient(app) as local:
        assert local.get("/api/v1/auth/status").json()["default_login_available"] is True
        login = local.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True
        assert local.get("/api/v1/system").status_code == 403
        changed = local.post("/api/v1/auth/change-password", json={"new_password": "an-updated-secure-password"})
        assert changed.status_code == 200
        assert local.get("/api/v1/system").status_code == 200
        assert local.get("/api/v1/auth/status").json()["default_login_available"] is False
    test_engine.dispose()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_system_metrics_schema(client):
    response = client.get("/api/v1/system")
    assert response.status_code == 200
    body = response.json()
    assert {"platform", "hostname", "cpu_percent", "per_cpu_percent", "memory_total", "memory_used",
            "memory_available", "swap_total", "swap_used", "disks", "networks", "uptime_seconds",
            "load_average", "temperatures"} <= body.keys()
    assert isinstance(body["disks"], list)
    assert isinstance(body["networks"], list)


def test_processes_use_shared_schema(client):
    response = client.get("/api/v1/processes")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    if response.json():
        assert {"pid", "name", "username", "cpu_percent", "memory_bytes", "executable", "command_line",
                "parent_pid", "status"} <= response.json()[0].keys()


def test_capabilities_identify_active_platform(client):
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    assert response.json()["platform"] in {"linux", "windows"}


def test_optional_integrations_endpoint_is_available(client):
    response = client.get("/api/v1/integrations")
    assert response.status_code == 200
    assert set(response.json()) == {"docker", "pm2"}
    for integration in response.json().values():
        assert {"installed", "available", "status", "message"} <= integration.keys()


def test_discord_alert_api_masks_secret_and_requires_webhook_when_enabled(client, monkeypatch):
    from app.integrations.discord_alerts import (DEFAULT_EVENTS, HISTORY_WEBHOOK_USERNAME, WEBHOOK_AVATAR_URL,
                                                 update_config, update_history_config)
    update_config(enabled=False, webhook_url="", events=DEFAULT_EVENTS, minimum_interval_seconds=0)
    update_history_config(enabled=False, webhook_url="", webhook_username=HISTORY_WEBHOOK_USERNAME,
                          avatar_url=WEBHOOK_AVATAR_URL)
    assert client.put("/api/v1/docker/alerts/discord", json={"enabled": True}).status_code == 400
    response = client.put("/api/v1/docker/alerts/discord", json={
        "enabled": True, "webhook_url": "https://discord.com/api/webhooks/123/secret",
        "events": {"start": True, "stop": True, "crash": True, "restart": True},
        "minimum_interval_seconds": 30,
        "webhook_username": "Custom Docker bot",
        "avatar_url": "https://example.com/docker.png",
    })
    assert response.status_code == 200
    assert "secret" not in response.text
    assert client.get("/api/v1/docker/alerts/discord").json()["configured"] is True
    docker_config = client.get("/api/v1/docker/alerts/discord").json()
    assert docker_config["webhook_username"] == "Custom Docker bot"
    assert docker_config["avatar_url"] == "https://example.com/docker.png"
    history = client.put("/api/v1/management/metrics/discord", json={
        "enabled": True, "webhook_url": "https://discord.com/api/webhooks/456/history-secret",
        "webhook_username": "Custom History bot", "avatar_url": "https://example.com/history.png",
    })
    assert history.status_code == 200, history.text
    assert history.json()["webhook_username"] == "Custom History bot"
    assert history.json()["avatar_url"] == "https://example.com/history.png"
    assert "history-secret" not in history.text
    assert client.get("/api/v1/management/metrics/discord").json()["webhook_username"] == "Custom History bot"
    monkeypatch.setattr("app.api.routes.send_test_message", lambda channel: None)
    assert client.post("/api/v1/management/metrics/discord/test").status_code == 200
    assert client.get("/api/v1/docker/alerts/discord").json()["webhook_username"] == "Custom Docker bot"
    update_config(enabled=False, webhook_url="", events=DEFAULT_EVENTS, minimum_interval_seconds=0)


def test_endpoint_check_records_one_audit_entry_per_manual_check(client, monkeypatch):
    targets = [
        {"id": f"target-{index}", "name": f"Endpoint {index}", "url": f"https://example{index}.com/health"}
        for index in range(3)
    ]
    original_settings = management.management_settings
    monkeypatch.setattr(management, "management_settings", lambda: {**original_settings(), "endpoint_targets": targets})
    monkeypatch.setattr(management, "check_endpoint", lambda target: {**target, "status": "healthy", "message": "OK"})

    response = client.post("/api/v1/management/endpoints/check")

    assert response.status_code == 200
    assert len(response.json()) == len(targets)
    audit = [row for row in management.audit_entries() if row["action"] == "network.endpoint.check"]
    assert len(audit) == 1
    assert audit[0]["target"] == "3 endpoint(s)"
    assert audit[0]["details"] == "Checked 3 endpoint(s): 3 healthy, 0 unhealthy."


def test_theme_update_accepts_post_for_older_frontend_clients(client):
    response = client.post("/api/v1/auth/preferences/theme", json={"theme": "monochrome"})
    assert response.status_code == 200
    assert client.get("/api/v1/auth/preferences").json()["theme"] == "monochrome"


def test_guest_permissions_are_enforced_by_api(client):
    response = client.post("/api/v1/management/users", json={
        "username": "guest-limited", "password": "another-long-password", "role": "guest",
        "permissions": ["view_dashboard"], "theme": "synfull",
    })
    account = response.json()
    assert response.status_code == 200
    admin_cookie = client.cookies.get(management.SESSION_COOKIE)
    guest_session = management.issue_session("guest-limited")
    client.cookies.set(management.SESSION_COOKIE, guest_session)
    assert client.get("/api/v1/system").status_code == 200
    assert client.get("/api/v1/processes").status_code == 403
    assert client.get("/api/v1/management/users").status_code == 403
    client.cookies.set(management.SESSION_COOKIE, admin_cookie)
    assert client.put(f"/api/v1/management/users/{account['id']}", json={
        "role": "guest", "enabled": False, "permissions": ["view_dashboard"], "theme": "synfull",
    }).status_code == 200
    client.cookies.set(management.SESSION_COOKIE, guest_session)
    assert client.get("/api/v1/system").status_code == 401


def test_user_account_api_requires_admin_and_returns_persisted_preferences(client):
    response = client.post("/api/v1/management/users", json={
        "username": "guest-one", "password": "another-long-password", "role": "guest",
        "permissions": ["view_dashboard"], "theme": "monochrome",
    })
    assert response.status_code == 200, response.text
    account = response.json()
    assert account["theme"] == "monochrome"
    assert account["permissions"] == ["view_dashboard"]
    assert client.put(f"/api/v1/management/users/{account['id']}", json={
        "role": "guest", "enabled": True, "permissions": ["view_network"], "theme": "hacker",
    }).json()["permissions"] == ["view_network"]
    assert client.get("/api/v1/auth/preferences").json()["theme"] == "synfull"
    assert client.put("/api/v1/auth/preferences/theme", json={"theme": "oled"}).json() == {"theme": "oled"}
    assert client.get("/api/v1/auth/preferences").json()["theme"] == "oled"


def test_docker_management_routes_validate_actions_and_identifiers(client):
    assert client.post("/api/v1/docker/containers/api/remove").status_code == 400
    assert client.post("/api/v1/docker/containers/invalid%2Fname/stop").status_code in {400, 404, 405}
    assert client.get("/api/v1/docker/containers/api/logs?tail=0").status_code == 422


def test_settings_summary_exposes_paths_without_secrets(client):
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert {"bind_host", "bind_port", "state_dir", "database_path", "log_dir", "collection_interval_seconds"} <= body.keys()
    assert "initial_admin_token" not in body


def test_management_routes_require_authentication():
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/system").status_code == 401
        assert anonymous.get("/api/v1/management/services").status_code == 401
