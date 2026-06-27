from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class MqttSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MQTT_", env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore")

    host: str = "localhost"
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    enabled: bool = False                    # opt-in; set MQTT_ENABLED=true to activate
    discovery_prefix: str = "homeassistant"


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Energipays credentials
    energipays_email: str = ""
    energipays_password: str = ""
    energipays_device_id: str = ""          # auto-discovered from /api/devices if blank
    energipays_key: str = ""                 # AES key override (base64); blank = auto-extract

    # Polling
    poll_interval: int = 60                  # seconds between Energipays API polls

    # Server
    admin_port: int = 8080
    admin_host: str = "0.0.0.0"

    # Storage
    data_dir: str = "./data"                 # SQLite + cache files live here
    raw_age_days: int = 7                    # full-resolution retention
    retention_days: int = 30                 # archive retention

    # Timezone (used for cloud stats API calls)
    timezone: str = "Australia/Sydney"

    # Logging
    log_level: str = "INFO"

    @property
    def data_path(self) -> pathlib.Path:
        return pathlib.Path(self.data_dir)

    @property
    def db_path(self) -> pathlib.Path:
        return self.data_path / "bridge.db"
