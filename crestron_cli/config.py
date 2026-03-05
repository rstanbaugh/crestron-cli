from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    home_ip: str
    auth_token: str
    timeout_s: float
    base_url: str


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"missing required env var {name}")
    return value


def load_config() -> Config:
    home_ip = _required_env("CRESTRON_HOME_IP")
    auth_token = _required_env("CRESTRON_AUTH_TOKEN")

    timeout_raw = os.getenv("CRESTRON_TIMEOUT_S", "10").strip()
    try:
        timeout_s = float(timeout_raw)
    except Exception:
        raise ConfigError("CRESTRON_TIMEOUT_S must be a number")
    if timeout_s <= 0:
        raise ConfigError("CRESTRON_TIMEOUT_S must be > 0")

    base_url = f"http://{home_ip}/cws/api"
    return Config(home_ip=home_ip, auth_token=auth_token, timeout_s=timeout_s, base_url=base_url)
