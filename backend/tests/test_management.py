import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import management


@pytest.fixture
def isolated_management(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'management.sqlite3'}", connect_args={"check_same_thread": False})
    management.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(management, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))
    monkeypatch.setattr(management, "get_settings", lambda: SimpleNamespace(
        state_dir=tmp_path, initial_admin_token=None, metric_retention_days=30, collection_interval_seconds=5,
    ))
    management._login_failures.clear()
    yield tmp_path
    test_engine.dispose()


def test_first_admin_setup_hashes_password_and_issues_revocable_session(isolated_management):
    management.ensure_setup_token()
    token_path = management.setup_token_path()
    setup_token = token_path.read_text(encoding="utf-8")
    session_token = management.create_admin(setup_token=setup_token, username="operator", password="a-long-and-unique-password")
    assert not token_path.exists()
    assert management.get_session_username(session_token) == "operator"
    with management.SessionLocal() as session:
        account = session.get(management.AdminAccount, 1)
        assert account.password_hash != "a-long-and-unique-password"
        assert account.password_salt
    with pytest.raises(ValueError, match="already been completed"):
        management.create_admin(setup_token=setup_token, username="second", password="a-long-and-unique-password")
    management.logout(session_token, "operator")
    assert management.get_session_username(session_token) is None


def test_default_admin_bootstrap_requires_password_rotation(isolated_management):
    assert management.ensure_default_admin() is True
    assert management.ensure_default_admin() is False
    session_token = management.login(username="admin", password="admin", client_ip="127.0.0.1")
    assert management.get_session_username(session_token) == "admin"
    assert management.current_user("admin")["must_change_password"] is True
    new_token = management.change_password("admin", "a-secure-custom-password")
    assert management.get_session_username(new_token) == "admin"
    assert management.current_user("admin")["must_change_password"] is False
    with pytest.raises(ValueError, match="only available"):
        management.change_password("admin", "another-secure-password")


def test_setup_token_and_login_rate_limit_are_enforced(isolated_management):
    management.ensure_setup_token()
    with pytest.raises(ValueError, match="Invalid setup token"):
        management.create_admin(setup_token="wrong-token-value-that-is-long-enough", username="operator", password="a-long-and-unique-password")
    token = management.setup_token_path().read_text(encoding="utf-8")
    management.create_admin(setup_token=token, username="operator", password="a-long-and-unique-password")
    for _ in range(10):
        with pytest.raises(ValueError, match="Invalid username or password"):
            management.login(username="operator", password="incorrect-password", client_ip="192.0.2.3")
    with pytest.raises(PermissionError, match="Too many"):
        management.login(username="operator", password="a-long-and-unique-password", client_ip="192.0.2.3")


def test_service_actions_require_allowlist_and_use_shell_free_command(isolated_management, monkeypatch):
    management.update_service_allowlist(["web.service"], username="operator")
    with patch.object(management, "_run", return_value=(0, "", "")) as run:
        assert management.service_action("web.service", "restart", username="operator")["status"] == "requested"
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][-2:] == ["stop", "web.service"]
    assert run.call_args_list[1].args[0][-2:] == ["start", "web.service"]
    with patch.object(management, "_run") as run:
        with pytest.raises(PermissionError, match="not in the configured"):
            management.service_action("other.service", "stop", username="operator")
        run.assert_not_called()


def test_target_and_threshold_validation_and_backup_freshness(isolated_management, tmp_path):
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        management.save_endpoint_targets([{"name": "bad", "url": "file:///private"}], username="operator")
    with pytest.raises(ValueError, match="without embedded credentials"):
        management.save_endpoint_targets([{"name": "bad", "url": "https://user:pass@example.com/health"}], username="operator")
    with patch.object(management.socket, "getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.2", 80))]):
        with pytest.raises(ValueError, match="public addresses"):
            management.save_endpoint_targets([{"name": "internal", "url": "http://internal.example/health"}], username="operator")
    with pytest.raises(ValueError, match="thresholds"):
        management.save_thresholds({"cpu_percent": 101, "memory_percent": 90, "disk_percent": 90}, username="operator")

    backup = tmp_path / "backup.tar"
    backup.write_text("archive", encoding="utf-8")
    management.save_backup_targets([{"name": "nightly", "path": str(backup), "max_age_hours": 48}], username="operator")
    result = management.backup_results()[0]
    assert result["status"] == "healthy"
    assert result["age_hours"] <= 0.1


def test_user_accounts_persist_themes_permissions_and_enforce_last_admin(isolated_management):
    admin = management.create_user(username="admin-two", password="a-long-and-unique-password", role="admin",
                                   permissions=["*"], theme="monochrome")
    guest = management.create_user(username="guest-one", password="another-long-password", role="guest",
                                   permissions=["view_dashboard", "manage_network"], theme="hacker")
    assert guest["permissions"] == ["manage_network", "view_dashboard"]
    assert management.current_user("admin-two")["theme"] == "monochrome"
    changed = management.update_user(guest["id"], role="guest", enabled=True,
                                    permissions=["view_network"], theme="oled")
    assert changed["permissions"] == ["view_network"]
    assert changed["theme"] == "oled"
    with pytest.raises(ValueError, match="At least one enabled administrator"):
        management.update_user(admin["id"], role="guest", enabled=True, permissions=[])


def test_endpoint_redirects_are_reported_as_healthy_and_persisted(isolated_management, monkeypatch):
    monkeypatch.setattr(management, "_validate_endpoint_host", lambda parsed: None)
    response = HTTPError("https://www.youtube.com", 302, "Found", {}, None)
    opener = Mock()
    opener.open.side_effect = response
    monkeypatch.setattr(management, "build_opener", lambda handler: opener)
    target = {"id": "youtube-target", "name": "YouTube", "url": "https://www.youtube.com/"}

    saved_target = management.save_endpoint_targets([{"name": "YouTube", "url": "https://www.youtube.com/"}], username="operator")[0]

    result = management.check_endpoint(saved_target)

    assert result["status"] == "healthy"
    assert result["response_code"] == 302
    assert "redirected" in result["message"]
    assert management.endpoint_results()[0]["response_code"] == 302


def test_audit_log_captures_allowlist_and_denied_service_action(isolated_management):
    management.update_service_allowlist(["web.service"], username="operator")
    with pytest.raises(PermissionError):
        management.service_action("other.service", "restart", username="operator")
    rows = management.audit_entries()
    assert any(row["action"] == "security.service_allowlist.update" for row in rows)
    denied = next(row for row in rows if row["action"] == "service.restart")
    assert denied["outcome"] == "denied"
