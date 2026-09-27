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

    # Mock PLC (app.sim) bind address. Ports < 1024 need root outside Docker.
    sim_host: str = "0.0.0.0"
    sim_port: int = Field(502, ge=0, le=65535)
    sim_behavior: bool = True             # run simulated ladder + plant (needs the full tag map)
    sim_tick_s: float = Field(0.2, gt=0)  # simulation step
    sim_travel_s: float = Field(20.0, gt=0)  # simulated end-to-end travel time
    sim_fake_pi_battery: bool = True      # mock writes battery_pct + pi_heartbeat itself (no Tuya)

    # Tuya Cloud (battery % from the MPPT controller). Empty = battery feed disabled.
    tuya_access_id: str = ""
    tuya_access_secret: SecretStr = SecretStr("")
    tuya_api_endpoint: str = ""
    tuya_device_id: str = ""
    tuya_battery_dp: str = ""             # DP code of battery %, see `python -m app.tuya`
    tuya_battery_scale: float = Field(1.0, gt=0)   # battery % = DP value * scale
    tuya_timeout_s: float = Field(10.0, gt=0)
    tuya_poll_interval_s: float = Field(60.0, ge=10)
    battery_max_age_s: float = Field(300.0, gt=0)  # older Tuya reading -> stop the heartbeat
    pi_heartbeat_interval_s: float = Field(2.0, gt=0)

    # SecretStr hides the value in repr/logs; call .get_secret_value() only where needed.
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None

    history_db: Path = Path("logs/gateway.db")
    snapshot_interval_s: float = Field(10.0, gt=0)
    history_retention_days: float = Field(30.0, gt=0)

    log_dir: Path = Path("logs")
    log_level: str = "INFO"

    def missing_tuya_keys(self) -> list[str]:
        """Tuya settings still empty (all needed for the battery feed)."""
        values = {
            "TUYA_ACCESS_ID": self.tuya_access_id,
            "TUYA_ACCESS_SECRET": self.tuya_access_secret.get_secret_value(),
            "TUYA_API_ENDPOINT": self.tuya_api_endpoint,
            "TUYA_DEVICE_ID": self.tuya_device_id,
            "TUYA_BATTERY_DP": self.tuya_battery_dp,
        }
        return [key for key, value in values.items() if not value.strip()]
