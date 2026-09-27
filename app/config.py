"""Runtime settings loaded from environment / .env (see .env.example)."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    plc_host: str
    plc_port: int = Field(502, ge=1, le=65535)
    plc_unit_id: int = Field(1, ge=0, le=247)
    plc_timeout_s: float = Field(2.0, gt=0)
    plc_poll_interval_s: float = Field(1.0, gt=0)

    plc_tags_file: Path = Path("config/plc_tags.yaml")

    # SecretStr hides the value in repr/logs; call .get_secret_value() only where needed.
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None

    log_dir: Path = Path("logs")
    log_level: str = "INFO"
