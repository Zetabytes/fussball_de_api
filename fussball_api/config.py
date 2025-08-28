from typing import Optional
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables or a .env file.

    :ivar API_KEY: The secret key to protect the API endpoints.
    :ivar CACHE_TTL_GAMES: TTL for game-related caches in seconds.
    :ivar CACHE_TTL_TABLE: TTL for table caches in seconds.
    :ivar CACHE_TTL_TEAMS: TTL for club team list caches in seconds.
    :ivar PREWARM_CLUB_ID: If set, proactively caches all data for this club ID.
    :ivar PREWARM_INTERVAL_SECONDS: Interval for the pre-warming job in seconds.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Security
    API_KEY: str = "your-secret-api-key"

    # Logging
    LOG_LEVEL: str = "INFO"

    # Cache settings (in seconds)
    CACHE_TTL_GAMES: int = 900  # 15 minutes
    CACHE_TTL_TABLE: int = 3600  # 1 hour
    CACHE_TTL_TEAMS: int = 7200  # 2 hours
    CACHE_TTL_FONT: int = 86400  # 24 hours

    # Proactive Caching (Pre-warming)
    PREWARM_CLUB_ID: Optional[str] = None
    PREWARM_INTERVAL_SECONDS: int = 300  # 5 minutes

    # Cache directory (configurable via environment variable)
    CACHE_DIR: Path = Path("./cache_payloads")


settings = Settings()
