import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CONFIG_FILE_PATH = Path(__file__).parent / ".." / "config.yaml"


class MonitoredHostConfig(BaseModel):
    """Configuration for a single monitored host."""

    alias: str
    check_gpu: bool = Field(default=True)


class HostConnectionDetails(BaseModel):
    """Configuration for connection details stored in host_details."""

    hostname: str
    user: str
    jump_host_alias: str | None = None  # Optional jump host per target


class AppConfig(BaseModel):
    """Structure for validating the configuration file."""

    page_title: str = Field(default="Remote Monitor")
    jump_host: str | None = Field(default=None)
    host_details: dict[str, HostConnectionDetails] = Field(default_factory=dict)
    monitored_hosts: list[MonitoredHostConfig] = Field(default_factory=list)
    refresh_interval_no_clients_sec: int = Field(
        default=1800, ge=600
    )  # Fetch every N minutes when no clients (default 15 mins)
    refresh_interval_clients_sec: int = Field(
        default=300, ge=60
    )  # Fetch every K minutes when clients connected (default 1 min)


def load_config() -> AppConfig:
    """Load and validate the configuration from config.yaml."""
    logger.info("Loading configuration from: %s", CONFIG_FILE_PATH)
    if not CONFIG_FILE_PATH.exists():
        msg = f"Configuration file not found: {CONFIG_FILE_PATH}"
        raise FileNotFoundError(msg)

    with CONFIG_FILE_PATH.open() as f:
        raw_config_data = yaml.safe_load(f)
        if not raw_config_data:
            msg = "Configuration file is empty or invalid YAML."
            raise ValueError(msg)

    # Pydantic will now handle validation of host_details and monitored_hosts directly
    # based on the AppConfig model structure. No need for manual processing here.
    logger.debug("Raw config data loaded: %s", raw_config_data)
    validated_config = AppConfig(**raw_config_data)
    logger.info("Configuration loaded and validated successfully.")
    return validated_config


# Load config once on module import
try:
    settings = load_config()
except (FileNotFoundError, RuntimeError, Exception):  # Catch broader exceptions during initial load
    logger.exception("FATAL ERROR loading configuration")
    # Provide minimal defaults if loading fails
    settings = AppConfig(
        page_title="Remote Monitor (Config Error)", jump_host=None, host_details={}, monitored_hosts=[]
    )
