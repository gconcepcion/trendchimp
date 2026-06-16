from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlpacaSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_key: str = ""
    secret_key: str = ""
    paper: bool = True
    data_feed: str = "iex"  # "iex" (free) | "sip" (paid) | "delayed_sip"


class TradingSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    symbols: list[str] = ["AAPL"]
    timeframe: str = "1Day"  # Turtle is a daily-bar system by default
    dry_run: bool = False
    # If set and the file exists, the bot trades the symbols from this universe
    # file (written by `trendchimp screen`) instead of `symbols`.
    universe_file: str = ""


class StrategySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    name: str = "donchian_breakout"
    params: dict[str, Any] = {}


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    risk_per_trade_pct: float = 0.01   # fraction of equity risked per N-unit (the "1%" rule)
    atr_stop_mult: float = 2.0         # protective stop placed 2N (2×ATR) from entry
    max_open_positions: int = 10       # hard cap on concurrent positions (long or short)
    max_units_per_symbol: int = 1      # v1: no pyramiding (hook for Turtle 4-unit add-ons)
    daily_loss_limit_pct: float = 0.02  # kill-switch: halt if daily P&L <= -2% of equity
    max_drawdown_pct: float = 0.20      # kill-switch: halt if peak-to-now drawdown >= 20%
    max_position_pct: float = 0.20      # hard cap on any single unit's notional vs equity
    allow_short: bool = True
    # Startup stop-recovery: stop distance used only when ATR can't be computed.
    recovery_fallback_stop_pct: float = 0.10
    # A held position whose symbol drops out of the traded universe is handed off to
    # a broker GTC trailing stop at this percent below/above the running price.
    orphan_trailing_stop_pct: float = 0.05


class ScreenerSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    anthropic_api_key: str = ""
    model: str = "claude-opus-4-8"   # cheaper drop-in: claude-sonnet-4-6
    top_n_technical: int = 30        # breakout candidates passed to Claude
    final_picks: int = 10            # tickers Claude selects for the universe
    lookback_days: int = 260         # daily bars fetched per symbol (SMA200 + buffer)
    cache_dir: str = "./cache"       # where the S&P 500 list is cached


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    level: str = "INFO"
    format: Literal["console", "json"] = "console"
    audit_log_path: str = "./logs/trades.jsonl"


class TrendChimpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRENDCHIMP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca: AlpacaSettings = AlpacaSettings()
    trading: TradingSettings = TradingSettings()
    strategy: StrategySettings = StrategySettings()
    risk: RiskSettings = RiskSettings()
    screener: ScreenerSettings = ScreenerSettings()
    logging: LoggingSettings = LoggingSettings()

    # Must be set explicitly to enable live trading alongside paper=False
    live_trading_confirmed: bool = False

    @model_validator(mode="after")
    def _enforce_live_trading_guard(self) -> "TrendChimpSettings":
        if not self.alpaca.paper and not self.live_trading_confirmed:
            raise ValueError(
                "Live trading requires TRENDCHIMP_LIVE_TRADING_CONFIRMED=true. "
                "Set both TRENDCHIMP_ALPACA__PAPER=false and "
                "TRENDCHIMP_LIVE_TRADING_CONFIRMED=true to proceed."
            )
        return self

    @model_validator(mode="after")
    def _require_credentials(self) -> "TrendChimpSettings":
        if not self.alpaca.api_key or not self.alpaca.secret_key:
            raise ValueError(
                "Alpaca credentials are required. Set TRENDCHIMP_ALPACA__API_KEY "
                "and TRENDCHIMP_ALPACA__SECRET_KEY."
            )
        return self
