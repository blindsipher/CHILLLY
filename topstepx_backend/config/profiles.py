import os
from typing import Any, Dict, List

import yaml
from pydantic import BaseSettings, Field


class BaseProfile(BaseSettings):
    """Base configuration profile using Pydantic settings."""

    username: str = ""
    api_key: str = ""
    account_id: int = 0
    account_name: str = ""
    projectx_base_url: str = "https://api.topstepx.com"
    projectx_user_hub_url: str = "https://rtc.topstepx.com/hubs/user"
    projectx_market_hub_url: str = "https://rtc.topstepx.com/hubs/market"
    database_path: str = "data/topstepx.db"
    log_level: str = "INFO"
    environment: str = "development"
    live_mode: bool = True
    default_contracts: List[str] = Field(default_factory=list)
    event_backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    use_uvloop: bool = False
    risk_max_position_size: int = 5
    risk_max_daily_loss: float = 1000.0
    risk_max_order_size: int = 5
    risk_max_orders_per_minute: int = 10

    @classmethod
    def customise_sources(cls, init_settings, env_settings, file_secret_settings):
        return (
            env_settings,
            cls.yaml_config_settings,
            cls.env_file_settings,
            init_settings,
            file_secret_settings,
        )

    @classmethod
    def env_file_settings(cls, settings: BaseSettings) -> Dict[str, Any]:
        """Load settings from a .env file if present."""
        env_file = os.getenv("ENV_FILE", ".env")
        if os.path.exists(env_file):
            from dotenv import dotenv_values

            return dotenv_values(env_file)
        return {}

    @classmethod
    def yaml_config_settings(cls, settings: BaseSettings) -> Dict[str, Any]:
        """Load settings from a YAML file if specified."""
        env = os.getenv("ENVIRONMENT", "development")
        default_path = os.path.join(os.path.dirname(__file__), f"{env}.yaml")
        yaml_path = os.getenv("CONFIG_YAML", default_path)
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except yaml.YAMLError:
                return {}
        return {}

    class Config:
        env_file = None  # handled manually above


class DevelopmentProfile(BaseProfile):
    environment: str = "development"


class StagingProfile(BaseProfile):
    environment: str = "staging"


class ProductionProfile(BaseProfile):
    environment: str = "production"
