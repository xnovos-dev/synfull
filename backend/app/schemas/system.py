from pydantic import BaseModel


class SystemResponse(BaseModel):
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


class ProcessResponse(BaseModel):
    pid: int
    name: str | None
    username: str | None
    cpu_percent: float | None
    memory_bytes: int | None
    executable: str | None
    command_line: list[str] | None
    parent_pid: int | None
    status: str | None
