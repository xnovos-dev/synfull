import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.integrations import discord_alerts as alerts


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'alerts.sqlite3'}", connect_args={"check_same_thread": False})
    alerts.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(alerts, "engine", test_engine)
    monkeypatch.setattr(alerts, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))
    alerts.update_config(enabled=False, webhook_url="", events=alerts.DEFAULT_EVENTS, minimum_interval_seconds=0)


def test_update_settings_masks_webhook_and_preserves_secret_when_url_omitted(isolated_settings):
    saved = alerts.update_config(enabled=True, webhook_url="https://discord.com/api/webhooks/123/secret-token",
                                 events={"start": True, "stop": False, "crash": True, "restart": False},
                                 minimum_interval_seconds=15)
    assert saved["configured"] is True
    assert "secret-token" not in json.dumps(saved)
    assert saved["webhook_url_masked"].endswith("••••••••")
    unchanged = alerts.update_config(enabled=False, webhook_url=None, events=saved["events"], minimum_interval_seconds=0)
    assert unchanged["configured"] is True
    assert unchanged["enabled"] is False


def test_webhook_validation_rejects_non_discord_and_non_https_urls(isolated_settings):
    for url in ("http://discord.com/api/webhooks/1/token", "https://evil.example/api/webhooks/1/token",
                "https://discord.com.evil.example/api/webhooks/1/token"):
        with pytest.raises(ValueError):
            alerts.update_config(enabled=False, webhook_url=url, events=alerts.DEFAULT_EVENTS,
                                 minimum_interval_seconds=0)


def test_lifecycle_monitor_maps_events_filters_and_throttles(isolated_settings):
    alerts.update_config(enabled=True, webhook_url="https://discord.com/api/webhooks/1/token",
                         events={"start": True, "stop": True, "crash": True, "restart": False},
                         minimum_interval_seconds=100)
    monitor = alerts.DockerEventMonitor()
    with patch.object(alerts, "send_discord_message") as send:
        started = monitor.process_event({"Action": "start", "id": "abc", "Actor": {"Attributes": {"name": "api"}}})
        duplicate_start = monitor.process_event({"Action": "start", "id": "abc", "Actor": {"Attributes": {"name": "api"}}})
        clean_stop = monitor.process_event({"Action": "die", "id": "abc", "Actor": {"Attributes": {"name": "api", "exitCode": "0"}}})
        crash = monitor.process_event({"Action": "die", "id": "def", "Actor": {"Attributes": {"name": "worker", "exitCode": "137"}}})
    assert started is True
    assert duplicate_start is False
    assert clean_stop is True
    assert crash is True
    assert send.call_count == 3
    assert "api" in send.call_args_list[0].args[0]
    assert "Stopped." in send.call_args_list[1].args[0]
    assert "Crashed unexpectedly." in send.call_args_list[2].args[0]
    assert send.call_args_list[0].kwargs["title"] == "Container started"
    assert "**Container:** `api`" in send.call_args_list[0].args[0]


def test_test_message_posts_json_with_timeout(isolated_settings):
    alerts.update_config(enabled=False, webhook_url="https://discord.com/api/webhooks/1/token",
                         events=alerts.DEFAULT_EVENTS, minimum_interval_seconds=0)

    class Response:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *args): return False

    with patch.object(alerts, "urlopen", return_value=Response()) as post:
        alerts.send_test_message()
    request = post.call_args.args[0]
    assert request.full_url == "https://discord.com/api/webhooks/1/token"
    payload = json.loads(request.data)
    assert payload["username"] == "Synfull - AIO Monitoring Software"
    assert payload["avatar_url"] == alerts.WEBHOOK_AVATAR_URL
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "Webhook test"
    assert payload["embeds"][0]["color"] == 0x3B82F6
    assert "**Webhook connected successfully**" in payload["embeds"][0]["description"]
    assert post.call_args.kwargs["timeout"] == 8.0
