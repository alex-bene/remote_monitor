import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CONFIG_FILE_PATH = Path(__file__).parent / ".." / "config.yaml"


class MonitoredHostConfig(BaseModel):
    """Configuration for a single monitored host."""

    hostname: str
    check_gpu: bool = Field(default=True)  # New field to control GPU check


class AppConfig(BaseModel):
    """Structure for validating the configuration file."""

    page_title: str = Field(default="Remote Monitor")  # Added page title
    jump_host: str | None = Field(default=None)  # Made jump_host optional
    monitored_hosts: list[MonitoredHostConfig] = Field(default_factory=list)
    refresh_interval_no_clients_sec: int = Field(
        default=900, ge=60
    )  # Fetch every N minutes when no clients (default 15 mins)
    refresh_interval_clients_sec: int = Field(
        default=60, ge=30
    )  # Fetch every K minutes when clients connected (default 1 min)


def load_config() -> AppConfig:
    """Load and validate the configuration from config.yaml."""
    if not CONFIG_FILE_PATH.exists():
        msg = f"Configuration file not found: {CONFIG_FILE_PATH}"
        raise FileNotFoundError(msg)

    with CONFIG_FILE_PATH.open() as f:
        config_data = yaml.safe_load(f)

    monitored_hosts_data = config_data.get("monitored_hosts", [])
    # Convert list of dicts to list of MonitoredHostConfig models
    validated_monitored_hosts = [
        MonitoredHostConfig(**host_data) if isinstance(host_data, dict) else MonitoredHostConfig(hostname=host_data)
        for host_data in monitored_hosts_data
    ]
    config_data["monitored_hosts"] = validated_monitored_hosts
    return AppConfig(**config_data)


# Load config once on module import
try:
    settings = load_config()
except (FileNotFoundError, RuntimeError, Exception) as e:  # Catch broader exceptions during initial load
    logger.exception("FATAL ERROR loading configuration: %s", e)
    # Provide minimal defaults if loading fails
    settings = AppConfig(page_title="Remote Monitor (Error)", jump_host=None, monitored_hosts=[])
