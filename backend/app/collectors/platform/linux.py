import platform
import time

import psutil

from .base import SystemMetrics


class LinuxAdapter:
    def system_metrics(self) -> SystemMetrics:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({"device": part.device, "mountpoint": part.mountpoint, "filesystem": part.fstype,
                              "total": usage.total, "used": usage.used, "free": usage.free})
            except (PermissionError, OSError):
                continue
        io = psutil.net_io_counters(pernic=True) or {}
        networks = [{"name": name, "bytes_sent": counters.bytes_sent, "bytes_received": counters.bytes_recv}
                    for name, counters in io.items()]
        temps = None
        try:
            sensor_data = psutil.sensors_temperatures()
            temps = [{"sensor": name, "label": entry.label or name, "current": entry.current}
                     for name, entries in (sensor_data or {}).items() for entry in entries]
        except (AttributeError, OSError):
            pass
        load = list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None
        return SystemMetrics(platform.system(), platform.node(), psutil.cpu_percent(interval=None),
                             psutil.cpu_percent(interval=None, percpu=True), memory.total, memory.used,
                             memory.available, swap.total, swap.used, disks, networks,
                             max(0.0, time.time() - psutil.boot_time()), load, temps)

    def processes(self) -> list[dict]:
        result = []
        attrs = ["pid", "name", "username", "exe", "ppid", "status", "memory_info"]
        for proc in psutil.process_iter(attrs=attrs, ad_value=None):
            try:
                data = proc.info
                result.append({"pid": data["pid"], "name": data["name"], "username": data["username"],
                               "executable": data["exe"], "command_line": None,
                               "parent_pid": data["ppid"], "status": data["status"],
                               "memory_bytes": data["memory_info"].rss if data["memory_info"] else None,
                               "cpu_percent": proc.cpu_percent(interval=None)})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return result

    def capabilities(self) -> dict:
        systemd = platform.system() == "Linux" and __import__("os").path.exists("/run/systemd/system")
        return {"platform": "linux", "service_manager": "systemd" if systemd else None,
                "temperature": hasattr(psutil, "sensors_temperatures"),
                "load_average": hasattr(psutil, "getloadavg"), "process_management": False}
