from pydantic import BaseModel, Field


class AdminSetup(BaseModel):
    setup_token: str = Field(min_length=16, max_length=256)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class ServiceAllowlistUpdate(BaseModel):
    services: list[str] = Field(max_length=250)


class EndpointTarget(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=2048)


class EndpointTargetsUpdate(BaseModel):
    targets: list[EndpointTarget] = Field(max_length=100)


class BackupTarget(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1024)
    max_age_hours: int = Field(default=48, ge=1, le=8760)


class BackupTargetsUpdate(BaseModel):
    targets: list[BackupTarget] = Field(max_length=100)


class ThresholdUpdate(BaseModel):
    thresholds: dict[str, float]


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(pattern="^(admin|guest)$")
    permissions: list[str] = Field(default_factory=list, max_length=32)
    theme: str = Field(default="synfull", pattern="^(synfull|monochrome|hacker|light|oled)$")


class UserUpdate(BaseModel):
    role: str = Field(pattern="^(admin|guest)$")
    enabled: bool = True
    permissions: list[str] = Field(default_factory=list, max_length=32)
    password: str | None = Field(default=None, min_length=12, max_length=256)
    theme: str | None = Field(default=None, pattern="^(synfull|monochrome|hacker|light|oled)$")


class ThemeUpdate(BaseModel):
    theme: str = Field(pattern="^(synfull|monochrome|hacker|light|oled)$")
