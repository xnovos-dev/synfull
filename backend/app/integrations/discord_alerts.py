import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy import Boolean, Column, Integer, Text

from ..config import get_settings
from ..database import Base, SessionLocal, engine

logger = logging.getLogger(__name__)


class DiscordAlertConfig(Base):
    __tablename__ = "discord_alert_config"

    id = Column(Integer, primary_key=True, default=1)
    webhook_url = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=False)
    events_json = Column(Text, nullable=False, default='{"start":true,"stop":true,"crash":true,"restart":true}')
    minimum_interval_seconds = Column(Integer, nullable=False, default=60)
    webhook_username = Column(Text, nullable=False, default="Synfull - AIO Monitoring Software")
    avatar_url = Column(Text, nullable=False, default="https://api.iconify.design/lucide/activity.svg?color=%23ffffff")


class HistoryDiscordAlertConfig(Base):
    __tablename__ = "history_discord_alert_config"

    id = Column(Integer, primary_key=True, default=1)
    webhook_url = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=False)
    webhook_username = Column(Text, nullable=False, default="Synfull - Monitoring Alerts")
    avatar_url = Column(Text, nullable=False, default="https://api.iconify.design/lucide/activity.svg?color=%23ffffff")


Base.metadata.create_all(bind=engine)

DEFAULT_EVENTS = {"start": True, "stop": True, "crash": True, "restart": True}
EMBED_BLUE = 0x3B82F6
WEBHOOK_USERNAME = "Synfull - AIO Monitoring Software"
WEBHOOK_AVATAR_URL = "https://api.iconify.design/lucide/activity.svg?color=%23ffffff"
HISTORY_WEBHOOK_USERNAME = "Synfull - Monitoring Alerts"


def _migrate_docker_config_schema() -> None:
    from sqlalchemy import inspect
    columns = {column["name"] for column in inspect(engine).get_columns("discord_alert_config")}
    with engine.begin() as connection:
        if "webhook_username" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE discord_alert_config ADD COLUMN webhook_username TEXT NOT NULL DEFAULT 'Synfull - AIO Monitoring Software'")
        if "avatar_url" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE discord_alert_config ADD COLUMN avatar_url TEXT NOT NULL DEFAULT 'https://api.iconify.design/lucide/activity.svg?color=%23ffffff'")


_migrate_docker_config_schema()


def _config_from_db() -> DiscordAlertConfig:
    with SessionLocal() as session:
        config = session.get(DiscordAlertConfig, 1)
        if config is None:
            config = DiscordAlertConfig(id=1)
            session.add(config)
            session.commit()
            session.refresh(config)
        return config


def _history_config_from_db() -> HistoryDiscordAlertConfig:
    with SessionLocal() as session:
        config = session.get(HistoryDiscordAlertConfig, 1)
        if config is None:
            config = HistoryDiscordAlertConfig(id=1)
            session.add(config)
            session.commit()
            session.refresh(config)
        return config


def _validate_webhook_url(webhook_url: str) -> str:
    normalized_url = webhook_url.strip()
    parsed = urlparse(normalized_url)
    if (parsed.scheme != "https" or parsed.hostname != "discord.com"
            or not parsed.path.startswith("/api/webhooks/") or parsed.username or parsed.password):
        raise ValueError("Enter a valid Discord HTTPS webhook URL")
    return normalized_url


def _validate_identity(webhook_username: str, avatar_url: str) -> tuple[str, str]:
    username = webhook_username.strip()
    avatar = avatar_url.strip()
    if not username or len(username) > 80:
        raise ValueError("Webhook name must be between 1 and 80 characters")
    if avatar:
        parsed = urlparse(avatar)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Webhook avatar URL must be a valid HTTPS URL")
    return username, avatar


def _parse_events(raw: str) -> dict[str, bool]:
    try:
        events = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        events = {}
    return {key: bool(events.get(key, False)) for key in DEFAULT_EVENTS} if isinstance(events, dict) else DEFAULT_EVENTS.copy()


def public_config() -> dict[str, Any]:
    config = _config_from_db()
    masked = "https://discord.com/api/webhooks/••••••••" if config.webhook_url else ""
    return {"enabled": config.enabled, "configured": bool(config.webhook_url), "webhook_url_masked": masked,
            "webhook_username": config.webhook_username, "avatar_url": config.avatar_url,
            "events": _parse_events(config.events_json), "minimum_interval_seconds": config.minimum_interval_seconds}


def public_history_config() -> dict[str, Any]:
    config = _history_config_from_db()
    masked = "https://discord.com/api/webhooks/••••••••" if config.webhook_url else ""
    return {"enabled": config.enabled, "configured": bool(config.webhook_url), "webhook_url_masked": masked,
            "webhook_username": config.webhook_username, "avatar_url": config.avatar_url}


def update_history_config(*, enabled: bool, webhook_url: str | None, webhook_username: str, avatar_url: str) -> dict[str, Any]:
    username, avatar = _validate_identity(webhook_username, avatar_url)
    with SessionLocal() as session:
        config = session.get(HistoryDiscordAlertConfig, 1)
        if config is None:
            config = HistoryDiscordAlertConfig(id=1)
            session.add(config)
            session.flush()
        if webhook_url == "":
            config.webhook_url = ""
        elif webhook_url is not None:
            config.webhook_url = _validate_webhook_url(webhook_url)
        if enabled and not config.webhook_url:
            raise ValueError("Set a Discord webhook URL before enabling alerts")
        config.enabled = enabled
        config.webhook_username = username
        config.avatar_url = avatar
        session.commit()
    return public_history_config()


def update_config(*, enabled: bool, webhook_url: str | None, events: dict[str, bool], minimum_interval_seconds: int,
                  webhook_username: str = WEBHOOK_USERNAME, avatar_url: str = WEBHOOK_AVATAR_URL) -> dict[str, Any]:
    with SessionLocal() as session:
        config = session.get(DiscordAlertConfig, 1)
        if config is None:
            config = DiscordAlertConfig(id=1)
            session.add(config)
            session.flush()
        if webhook_url == "":
            config.webhook_url = ""
        elif webhook_url is not None:
            config.webhook_url = _validate_webhook_url(webhook_url)
        username, avatar = _validate_identity(webhook_username, avatar_url)
        if minimum_interval_seconds < 0 or minimum_interval_seconds > 3600:
            raise ValueError("Minimum alert interval must be between 0 and 3600 seconds")
        config.enabled = enabled
        config.events_json = json.dumps({key: bool(events.get(key, False)) for key in DEFAULT_EVENTS})
        config.minimum_interval_seconds = minimum_interval_seconds
        config.webhook_username = username
        config.avatar_url = avatar
        session.commit()
    return public_config()


def send_discord_message(content: str, *, title: str = "Monitoring alert", timeout_seconds: float = 8.0,
                         channel: str = "docker") -> None:
    if channel == "history":
        config = _history_config_from_db()
        default_username = HISTORY_WEBHOOK_USERNAME
        if not config.enabled or not config.webhook_url:
            raise ValueError("Configure and enable the History & Alerts Discord webhook first")
    else:
        config = _config_from_db()
        default_username = WEBHOOK_USERNAME
        if not config.webhook_url:
            raise ValueError("Configure a Discord webhook URL first")
    payload = {
        "username": config.webhook_username or default_username,
        "avatar_url": config.avatar_url or WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title[:256],
            "description": content[:4096],
            "color": EMBED_BLUE,
            "footer": {"text": "Synfull · AIO Monitoring Software"},
        }],
    }
    request = Request(config.webhook_url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json", "User-Agent": "Synfull/0.1"}, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {response.status}")
    except HTTPError as exc:
        raise RuntimeError(f"Discord webhook returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Could not reach Discord webhook. Check the connection and webhook URL.") from None


def send_test_message(channel: str = "docker") -> None:
    send_discord_message(
        "\n".join((
            "✅ **Webhook connected successfully**",
            "",
            "Synfull can now deliver monitoring alerts to this channel.",
        )),
        title="Webhook test",
        channel=channel,
    )


class DockerEventMonitor:
    def __init__(self, client_factory=None, interval_seconds: float = 2.0):
        self._client_factory = client_factory or self._default_client
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = None
        self._last_sent: dict[tuple[str, str], float] = {}
        self._previous_states: dict[str, tuple[str, str]] = {}

    @staticmethod
    def _default_client():
        import docker  # type: ignore[import-not-found]
        settings = get_settings()
        return docker.DockerClient(base_url=settings.docker_endpoint, timeout=5) if settings.docker_endpoint else docker.from_env(timeout=5)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="synfull-docker-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                with SessionLocal() as session:
                    config = session.get(DiscordAlertConfig, 1)
                    enabled = bool(config and config.enabled and config.webhook_url)
            except Exception as exc:
                logger.debug("Unable to read Docker alert settings: %s", exc)
                self._stop_event.wait(self._interval_seconds)
                continue
            if not enabled:
                self._stop_event.wait(self._interval_seconds)
                continue
            try:
                client = self._client_factory()
                self._client = client
                try:
                    for event in client.events(decode=True, filters={"type": "container", "event": ["start", "stop", "die", "restart"]}):
                        if self._stop_event.is_set():
                            return
                        self.process_event(event)
                finally:
                    if self._client is client:
                        self._client = None
                    close = getattr(client, "close", None)
                    if close:
                        close()
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.debug("Docker event monitor reconnecting after error: %s", exc)
                    self._stop_event.wait(self._interval_seconds)

    def process_event(self, event: dict[str, Any]) -> bool:
        config = _config_from_db()
        if not config.enabled or not config.webhook_url:
            return False
        action = str(event.get("Action") or event.get("status") or "").lower()
        actor = event.get("Actor") or {}
        attributes = actor.get("Attributes") or {}
        name = attributes.get("name") or event.get("id") or "unknown container"
        container_id = str(event.get("id") or event.get("ID") or name)[:64]
        if action == "die":
            try:
                exit_code = int(attributes.get("exitCode", "1"))
            except (TypeError, ValueError):
                exit_code = 1
            event_key = "stop" if exit_code == 0 else "crash"
        else:
            event_key = action
        events = _parse_events(config.events_json)
        if event_key not in events or not events[event_key]:
            return False
        previous = self._previous_states.get(container_id)
        if event_key == "crash" and previous and previous[0] == "stopped":
            return False
        if event_key == "start" and previous and previous[0] == "stopped" and events.get("restart"):
            event_key = "restart"
        self._previous_states[container_id] = ("stopped" if event_key in {"stop", "crash"} else "running", name)
        now = time.monotonic()
        throttle_key = (container_id, event_key)
        if now - self._last_sent.get(throttle_key, 0) < config.minimum_interval_seconds:
            return False
        labels = {"start": "started", "stop": "stopped", "crash": "crashed unexpectedly", "restart": "restarted"}
        titles = {"start": "Container started", "stop": "Container stopped", "crash": "Container crashed", "restart": "Container restarted"}
        safe_name = str(name).replace("`", "ˋ")
        try:
            send_discord_message(
                "\n".join((
                    f"🐳 **Container:** `{safe_name}`",
                    f"**ID:** `{container_id[:12]}`",
                    f"**Event:** {labels[event_key].capitalize()}.",
                )),
                title=titles[event_key],
            )
            self._last_sent[throttle_key] = now
            return True
        except Exception as exc:
            logger.warning("Unable to send Docker %s alert for %s: %s", event_key, name, exc)
            return False


monitor = DockerEventMonitor()
