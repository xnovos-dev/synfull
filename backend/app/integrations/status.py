import json
import re
import shutil
import subprocess
from typing import Any

from ..config import get_settings


COMMAND_TIMEOUT_SECONDS = 3
CONTAINER_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{1,64}$")
CONTAINER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def validate_container_identifier(container_id: str) -> str:
    if not isinstance(container_id, str) or not (CONTAINER_ID_PATTERN.fullmatch(container_id) or CONTAINER_NAME_PATTERN.fullmatch(container_id)):
        raise ValueError("Invalid Docker container identifier")
    return container_id


def docker_action(container_id: str, action: str) -> dict[str, Any]:
    identifier = validate_container_identifier(container_id)
    if action not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported Docker action")
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Docker SDK is not installed; container actions are disabled") from exc
    settings = get_settings()
    client = docker.DockerClient(base_url=settings.docker_endpoint, timeout=COMMAND_TIMEOUT_SECONDS) if settings.docker_endpoint else docker.from_env(timeout=COMMAND_TIMEOUT_SECONDS)
    container = client.containers.get(identifier)
    getattr(container, action)(timeout=10) if action in {"stop", "restart"} else getattr(container, action)()
    container.reload()
    return {"id": container.short_id, "name": container.name, "state": container.status, "action": action}


def docker_logs(container_id: str, tail: int = 300) -> dict[str, Any]:
    identifier = validate_container_identifier(container_id)
    if not 1 <= tail <= 2000:
        raise ValueError("Log tail must be between 1 and 2000 lines")
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Docker SDK is not installed; container logs are unavailable") from exc
    settings = get_settings()
    client = docker.DockerClient(base_url=settings.docker_endpoint, timeout=COMMAND_TIMEOUT_SECONDS) if settings.docker_endpoint else docker.from_env(timeout=COMMAND_TIMEOUT_SECONDS)
    container = client.containers.get(identifier)
    content = container.logs(tail=tail, timestamps=True, stdout=True, stderr=True)
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return {"id": container.short_id, "name": container.name, "logs": content, "tail": tail}


def _format_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _run(arguments: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
                                check=False, shell=False)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def docker_status() -> dict[str, Any]:
    settings = get_settings()
    docker_bin = shutil.which("docker")
    result: dict[str, Any] = {"installed": False, "available": False, "status": "unavailable",
                              "message": "Docker CLI is not installed.", "containers": []}
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError:
        docker_sdk = False
    else:
        docker_sdk = True
    result["sdk_installed"] = docker_sdk
    result["cli_installed"] = docker_bin is not None
    result["installed"] = docker_sdk or docker_bin is not None
    if docker_sdk:
        try:
            client = docker.DockerClient(base_url=settings.docker_endpoint, timeout=COMMAND_TIMEOUT_SECONDS) if settings.docker_endpoint else docker.from_env(timeout=COMMAND_TIMEOUT_SECONDS)
            containers = client.containers.list(all=True)
            normalized = []
            for container in containers:
                item = {"id": container.short_id, "name": container.name,
                        "image": str(container.image.tags[0] if container.image.tags else container.image.short_id),
                        "state": container.status, "cpu_percent": None, "memory_usage": None, "memory_percent": None}
                if container.status == "running":
                    try:
                        stats = container.stats(stream=False)
                        cpu = stats.get("cpu_stats", {})
                        previous_cpu = stats.get("precpu_stats", {})
                        cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - previous_cpu.get("cpu_usage", {}).get("total_usage", 0)
                        system_delta = cpu.get("system_cpu_usage", 0) - previous_cpu.get("system_cpu_usage", 0)
                        cpu_count = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage", [])) or 1
                        if cpu_delta >= 0 and system_delta > 0:
                            item["cpu_percent"] = round((cpu_delta / system_delta) * cpu_count * 100, 1)
                        memory = stats.get("memory_stats", {})
                        usage = memory.get("usage")
                        cache = memory.get("stats", {}).get("inactive_file", memory.get("stats", {}).get("cache", 0))
                        limit = memory.get("limit")
                        if usage is not None:
                            item["memory_usage"] = _format_size(max(0, usage - cache))
                        if usage is not None and limit:
                            item["memory_percent"] = round(max(0, usage - cache) / limit * 100, 1)
                    except Exception:
                        pass
                normalized.append(item)
            result.update(available=True, status="available", message="Docker daemon is reachable.", containers=normalized)
            result["running"] = sum(c["state"] == "running" for c in result["containers"])
            return result
        except Exception as exc:
            result.update(status="unavailable", message=f"Docker daemon is not reachable: {exc}")
    if docker_bin:
        code, stdout, stderr = _run([docker_bin, "ps", "--all", "--format", "{{json .}}"])
        if code == 0:
            containers = []
            for line in stdout.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                containers.append({"id": row.get("ID", ""), "name": row.get("Names", ""),
                                   "image": row.get("Image", ""), "state": row.get("State", "unknown")})
            stats_code, stats_output, _ = _run([docker_bin, "stats", "--no-stream", "--format", "{{json .}}"])
            if stats_code == 0:
                stats_by_name = {}
                for line in stats_output.splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stats_by_name[row.get("Name", "")] = row
                for container in containers:
                    stats = stats_by_name.get(container["name"])
                    if stats:
                        container.update(cpu_percent=stats.get("CPUPerc"), memory_usage=stats.get("MemUsage"),
                                         memory_percent=stats.get("MemPerc"))
                    else:
                        container.update(cpu_percent=None, memory_usage=None, memory_percent=None)
            else:
                for container in containers:
                    container.update(cpu_percent=None, memory_usage=None, memory_percent=None)
            result.update(available=True, status="available", message="Docker daemon is reachable.", containers=containers,
                          running=sum(c["state"] == "running" for c in containers))
        else:
            result.update(status="unavailable", message=(stderr.strip() or "Docker daemon is stopped or inaccessible."))
    return result


def pm2_status() -> dict[str, Any]:
    settings = get_settings()
    executable = settings.pm2_executable or shutil.which("pm2")
    result: dict[str, Any] = {"installed": executable is not None, "available": False,
                              "status": "unavailable", "message": "PM2 is not installed.", "processes": []}
    if executable is None:
        return result
    result["message"] = "PM2 is installed but its process list is unavailable."
    code, stdout, stderr = _run([executable, "jlist"])
    if code != 0:
        result["message"] = stderr.strip() or "PM2 process list is inaccessible for the configured user."
        return result
    try:
        rows = json.loads(stdout)
        if not isinstance(rows, list):
            raise ValueError("PM2 returned an unexpected process list")
    except (json.JSONDecodeError, ValueError) as exc:
        result["message"] = f"Could not parse PM2 process list: {exc}"
        return result
    processes = []
    for row in rows:
        env = row.get("pm2_env") or {}
        monit = row.get("monit") or {}
        processes.append({"id": row.get("pm_id"), "name": row.get("name"),
                          "status": env.get("status", "unknown"), "cpu_percent": monit.get("cpu"),
                          "memory_bytes": monit.get("memory"), "uptime_ms": env.get("pm_uptime")})
    result.update(available=True, status="available", message="PM2 process list is available.", processes=processes,
                  running=sum(p["status"] == "online" for p in processes))
    if settings.pm2_owner:
        result["configured_owner"] = settings.pm2_owner
    return result


def integrations_status() -> dict[str, dict[str, Any]]:
    return {"docker": docker_status(), "pm2": pm2_status()}
