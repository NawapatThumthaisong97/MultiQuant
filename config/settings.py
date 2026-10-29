"""
Application Configuration and Settings.
Built using Pydantic Settings for strict typing and environment parsing.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class AppSettings(BaseSettings):
    """Central configuration for MultiQuant engine."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH) if _ENV_PATH.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General System
    ENV: str = Field(default="development", description="development / production")
    LOG_LEVEL: str = Field(default="INFO")

    # Layer 1: Market Connectivity
    WS_BINANCE_URL: str = Field(
        default="wss://stream.binance.com:9443/ws", description="Public WebSocket stream endpoint"
    )
    DEFAULT_SYMBOLS: list[str] = Field(default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    WS_RECONNECT_MAX_RETRIES: int = Field(default=10)
    WS_PING_INTERVAL_SEC: int = Field(default=20)

    # Layer 3: Risk Sizing Defaults
    MAX_PORTFOLIO_RISK_PCT: float = Field(default=0.02, description="Max 2% capital risk per trade")
    MAX_DRAWDOWN_LIMIT_PCT: float = Field(
        default=0.15, description="15% max drawdown circuit breaker"
    )
    ATR_PERIOD: int = Field(default=14)
    ATR_MULTIPLIER: float = Field(default=2.0)

    # Layer 5: State & Persistence
    SQLITE_DB_PATH: str = Field(default="multiquant_state.db")
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_PASSWORD: str | None = None

    # Layer 6: Alerting
    DISCORD_WEBHOOK_URL: str | None = Field(default=None)
    PROMETHEUS_METRICS_PORT: int = Field(default=8000)


settings = AppSettings()
