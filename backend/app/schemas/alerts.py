from pydantic import BaseModel, Field, field_validator


DEFAULT_DOCKER_ALERT_EVENTS = {"start", "stop", "crash", "restart"}


class DiscordAlertUpdate(BaseModel):
    enabled: bool
    events: dict[str, bool] = Field(default_factory=lambda: {"start": True, "stop": True, "crash": True, "restart": True})
    minimum_interval_seconds: int = Field(default=60, ge=0, le=3600)
    webhook_url: str | None = None
    clear_webhook: bool = False

    @field_validator("events")
    @classmethod
    def validate_event_keys(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) != DEFAULT_DOCKER_ALERT_EVENTS:
            raise ValueError("Choose a value for each supported Docker alert event")
        return value
