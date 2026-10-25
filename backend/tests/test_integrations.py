import json
from unittest.mock import patch

from app.integrations import status


class FakeContainer:
    short_id = "abc123"
    name = "api"
    status = "running"

    def __init__(self):
        self.started = False
        self.stopped = False
        self.restarted = False

    def logs(self, **kwargs):
        assert kwargs == {"tail": 25, "timestamps": True, "stdout": True, "stderr": True}
        return b"2026-01-01T00:00:00Z service ready\n"

    def start(self):
        self.started = True
        self.status = "running"

    def stop(self, timeout):
        self.stopped = True
        self.status = "exited"

    def restart(self, timeout):
        self.restarted = True
        self.status = "running"

    def reload(self):
        pass


class FakeClient:
    def __init__(self, container):
        self.container = container
        self.containers = self

    def get(self, container_id):
        assert container_id == "abc123"
        return self.container


def test_missing_docker_and_pm2_are_reported_without_raising():
    with patch.object(status.shutil, "which", return_value=None), patch.dict("sys.modules", {"docker": None}):
        docker = status.docker_status()
        pm2 = status.pm2_status()
    assert docker["installed"] is False
    assert docker["available"] is False
    assert pm2["installed"] is False
    assert pm2["available"] is False


def test_pm2_running_process_stats_are_normalized():
    output = json.dumps([{
        "pm_id": 3,
        "name": "web-worker",
        "pm2_env": {"status": "online", "pm_uptime": 1234},
        "monit": {"cpu": 8.5, "memory": 2097152},
    }])
    with patch.object(status, "get_settings") as settings, \
         patch.object(status.shutil, "which", return_value="/usr/bin/pm2"), \
         patch.object(status, "_run", return_value=(0, output, "")):
        settings.return_value.pm2_executable = None
        settings.return_value.pm2_owner = "monitor"
        result = status.pm2_status()
    assert result["available"] is True
    assert result["running"] == 1
    assert result["processes"][0] == {
        "id": 3, "name": "web-worker", "status": "online", "cpu_percent": 8.5,
        "memory_bytes": 2097152, "uptime_ms": 1234,
    }
    assert result["configured_owner"] == "monitor"


def test_docker_container_logs_and_lifecycle_actions():
    container = FakeContainer()
    with patch.object(status, "get_settings") as settings, \
         patch("docker.DockerClient", return_value=FakeClient(container)):
        settings.return_value.docker_endpoint = "tcp://docker-host:2375"
        logs = status.docker_logs("abc123", 25)
        started = status.docker_action("abc123", "start")
        stopped = status.docker_action("abc123", "stop")
        restarted = status.docker_action("abc123", "restart")
    assert logs == {"id": "abc123", "name": "api", "logs": "2026-01-01T00:00:00Z service ready\n", "tail": 25}
    assert started["action"] == "start" and container.started
    assert stopped["action"] == "stop" and container.stopped
    assert restarted["action"] == "restart" and container.restarted


def test_docker_identifiers_and_actions_are_validated():
    import pytest
    with pytest.raises(ValueError):
        status.validate_container_identifier("../../other")
    with pytest.raises(ValueError):
        status.docker_action("api", "remove")
    with pytest.raises(ValueError):
        status.docker_logs("api", 0)


def test_docker_cli_container_stats_are_normalized():
    output = json.dumps({"ID": "abc123", "Names": "api", "Image": "example/api:latest", "State": "running"})
    stats = json.dumps({"Name": "api", "CPUPerc": "0.5%", "MemUsage": "20MiB / 1GiB", "MemPerc": "2%"})
    with patch.object(status, "get_settings") as settings, \
         patch.object(status.shutil, "which", return_value="docker"), \
         patch("builtins.__import__", side_effect=ImportError("SDK unavailable")), \
         patch.object(status, "_run", side_effect=[(0, output, ""), (0, stats, "")]):
        settings.return_value.docker_endpoint = None
        result = status.docker_status()
    assert result["available"] is True
    assert result["running"] == 1
    assert result["containers"] == [{"id": "abc123", "name": "api", "image": "example/api:latest", "state": "running",
                                     "cpu_percent": "0.5%", "memory_usage": "20MiB / 1GiB", "memory_percent": "2%"}]
