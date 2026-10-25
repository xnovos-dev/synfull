from dataclasses import dataclass
from typing import Protocol


@dataclass
class SystemMetrics:
    platform: str
    hostname: str
    cpu_percent: float | None
    per_cpu_percent: list[float] | None
    memory_total: int | None
    memory_used: int | None
    memory_available: int | None
    swap_total: int | None
    swap_used: int | None
    disks: list[dict]
    networks: list[dict]
    uptime_seconds: float | None
    load_average: list[float] | None
    temperatures: list[dict] | None


class PlatformAdapter(Protocol):
    def system_metrics(self) -> SystemMetrics: ...
    def processes(self) -> list[dict]: ...
    def capabilities(self) -> dict: ...
