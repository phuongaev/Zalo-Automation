from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiRequestConfig(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class LoginConfig(BaseModel):
    phone: str = ""
    password: str = ""


class AccountConfig(BaseModel):
    account_id: str
    enabled: bool = True
    emulator_name: str = ""
    emulator_index: int | None = None
    adb_serial: str
    login: LoginConfig = Field(default_factory=LoginConfig)


class GlobalConfig(BaseModel):
    dry_run: bool = True
    request_timeout_seconds: int = 45
    local_tmp_root: str = "tmp"
    screenshots_root: str = "screenshots"
    log_dir: str = "logs"
    zalo_package: str = "com.zing.zalo"
    emulator_media_dir: str = "/sdcard/Pictures/zalo_auto"
    adb_path: str = "adb"
    ldconsole_path: str = "D:\\LDPlayer\\LDPlayer3.0\\ldconsole.exe"
    launch_wait_seconds: int = 35
    after_launch_connect_seconds: int = 10
    batch_size: int = 3
    stop_emulator_after_run: bool = True
    keep_emulator_open_on_failure: bool = True
    tap_delay_seconds: float = 0.6
    type_delay_seconds: float = 0.8
    swipe_delay_seconds: float = 0.8
    step_delay_seconds: float = 1.2
    content_api: ApiRequestConfig


class AppFileConfig(BaseModel):
    global_: GlobalConfig = Field(alias="global")
    accounts: list[AccountConfig]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZALO_APP_", extra="ignore")

    config: str = "config/accounts.yaml"
    host: str = "0.0.0.0"
    port: int = 8787
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def load_app_config() -> AppFileConfig:
    path = Path(get_settings().config)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppFileConfig.model_validate(raw)
