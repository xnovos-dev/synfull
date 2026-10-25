from functools import lru_cache
import os
from pathlib import Path
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_state_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "Synfull" if sys.platform == "win32" else root / "synfull"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNFULL_", env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    state_dir: Path = Field(default_factory=default_state_dir)
    database_path: Path | None = None
    log_dir: Path | None = None
    collection_interval_seconds: float = 5.0
    metric_retention_days: int = 30
    development: bool = False
    docker_endpoint: str | None = None
    pm2_executable: str | None = None
    pm2_owner: str | None = None
    service_allowlist: str = ""
    initial_admin_token: str | None = None

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or self.state_dir / "synfull.sqlite3"

    @property
    def resolved_log_dir(self) -> Path:
        return self.log_dir or self.state_dir / "logs"

    def prepare_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
        self.resolved_log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
