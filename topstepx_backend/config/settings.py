import os
import re
from typing import Optional, List
from dataclasses import dataclass, field
from urllib.parse import urlparse
from dotenv import load_dotenv

from .profiles import (
    BaseProfile,
    DevelopmentProfile,
    ProductionProfile,
    StagingProfile,
)


@dataclass
class TopstepConfig:
    """Configuration for TopstepX API and backend settings."""

    username: str
    api_key: str
    account_id: int
    account_name: str
    # ProjectX Gateway API (Primary)
    projectx_base_url: str
    projectx_user_hub_url: str
    projectx_market_hub_url: str
    database_path: str
    log_level: str
    environment: str
    live_mode: bool = True  # Live mode by default
    default_contracts: List[str] = field(default_factory=list)  # Initial contract subscriptions
    # Event system
    event_backend: str = "memory"  # memory|redis
    redis_url: str = "redis://localhost:6379/0"
    use_uvloop: bool = False
    # Caching
    cache_backend: str = "memory"  # memory|redis
    cache_ttl: int = 3600  # seconds
    # Global risk thresholds
    risk_max_position_size: int = 5
    risk_max_daily_loss: float = 1000.0
    risk_max_order_size: int = 5
    risk_max_orders_per_minute: int = 10

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "TopstepConfig":
        """Load configuration from environment variables."""
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        return cls(
            username=os.getenv("TOPSTEP_USERNAME", ""),
            api_key=os.getenv("TOPSTEP_API_KEY", ""),
            account_id=int(os.getenv("TOPSTEP_ACCOUNT_ID", "0")),
            account_name=os.getenv("TOPSTEP_ACCOUNT_NAME", ""),
            # ProjectX Gateway API (Primary) - Production URLs, Simulated Trading
            projectx_base_url=os.getenv(
                "PROJECTX_BASE_URL", "https://api.topstepx.com"
            ),
            projectx_user_hub_url=os.getenv(
                "PROJECTX_USER_HUB_URL", "https://rtc.topstepx.com/hubs/user"
            ),
            projectx_market_hub_url=os.getenv(
                "PROJECTX_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market"
            ),
            database_path=os.getenv("DATABASE_PATH", "data/topstepx.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            environment=os.getenv("ENVIRONMENT", "development"),
            live_mode=os.getenv("LIVE_MODE", "true").lower() == "true",
            default_contracts=[
                c.strip()
                for c in os.getenv("DEFAULT_CONTRACTS", "").split(",")
                if c.strip()
            ],
            event_backend=os.getenv("EVENT_BACKEND", "memory"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            use_uvloop=os.getenv("USE_UVLOOP", "false").lower() == "true",
            cache_backend=os.getenv("CACHE_BACKEND", "memory"),
            cache_ttl=int(os.getenv("CACHE_TTL", "3600")),
            risk_max_position_size=int(
                os.getenv("RISK_MAX_POSITION_SIZE", "5")
            ),
            risk_max_daily_loss=float(
                os.getenv("RISK_MAX_DAILY_LOSS", "1000")
            ),
            risk_max_order_size=int(os.getenv("RISK_MAX_ORDER_SIZE", "5")),
            risk_max_orders_per_minute=int(
                os.getenv("RISK_MAX_ORDERS_PER_MINUTE", "10")
            ),
        )

    def validate(self) -> bool:
        """Validate that required configuration values are present and secure."""
        required_fields = [
            "username",
            "api_key",
            "projectx_base_url",
            "projectx_user_hub_url",
            "projectx_market_hub_url",
        ]
        for field in required_fields:
            value = getattr(self, field)
            if not value or (isinstance(value, str) and not value.strip()):
                raise ValueError(
                    f"Required configuration field '{field}' is missing or empty"
                )

        # Validate account_id
        if self.account_id <= 0:
            raise ValueError("Invalid account_id: must be positive integer")

        # Validate API key format (should be alphanumeric with potential special chars including base64)
        if not re.match(r"^[A-Za-z0-9_\-\.=+/]+$", self.api_key):
            raise ValueError("Invalid api_key format: contains invalid characters")

        if len(self.api_key) < 16:
            raise ValueError("Invalid api_key: too short (minimum 16 characters)")

        # Validate URLs
        url_fields = [
            "projectx_base_url",
            "projectx_user_hub_url",
            "projectx_market_hub_url",
        ]
        for field in url_fields:
            url = getattr(self, field)
            if not self._validate_url(url):
                raise ValueError(f"Invalid URL format for {field}: {url}")

        # Validate database path
        if not self.database_path:
            raise ValueError("Database path cannot be empty")

        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(
                f"Invalid log_level: {self.log_level}. Must be one of {valid_log_levels}"
            )

        # Validate environment
        valid_environments = ["development", "staging", "production"]
        if self.environment not in valid_environments:
            raise ValueError(
                f"Invalid environment: {self.environment}. Must be one of {valid_environments}"
            )

        # Validate event backend choice
        valid_backends = ["memory", "redis"]
        if self.event_backend not in valid_backends:
            raise ValueError(
                f"Invalid event_backend: {self.event_backend}. Must be one of {valid_backends}"
            )
        if self.event_backend == "redis" and not self.redis_url:
            raise ValueError("redis_url must be provided when using Redis event backend")

        if self.cache_backend not in valid_backends:
            raise ValueError(
                f"Invalid cache_backend: {self.cache_backend}. Must be one of {valid_backends}"
            )
        if self.cache_backend == "redis" and not self.redis_url:
            raise ValueError("redis_url must be provided when using Redis cache backend")

        return True

    def _validate_url(self, url: str) -> bool:
        """Validate URL format and security."""
        try:
            parsed = urlparse(url)

            # Must have scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False

            # Must be HTTPS in production
            if self.environment == "production" and parsed.scheme != "https":
                raise ValueError(f"Production environment requires HTTPS URLs: {url}")

            # Must be HTTP/HTTPS
            if parsed.scheme not in ["http", "https"]:
                return False

            # Basic hostname validation
            hostname = parsed.netloc.split(":")[0]  # Remove port if present
            if not re.match(r"^[a-zA-Z0-9\-\.]+$", hostname):
                return False

            return True

        except Exception:
            return False


# Global config instance
config: Optional[TopstepConfig] = None


PROFILE_MAP = {
    "development": DevelopmentProfile,
    "staging": StagingProfile,
    "production": ProductionProfile,
}


def _load_profile() -> BaseProfile:
    env = os.getenv("ENVIRONMENT", "development").lower()
    profile_cls = PROFILE_MAP.get(env, DevelopmentProfile)
    return profile_cls()


def get_config() -> TopstepConfig:
    """Get the global configuration instance."""
    global config
    if config is None:
        profile = _load_profile()
        config = TopstepConfig(**profile.dict())
        config.validate()
    return config
