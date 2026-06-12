from __future__ import annotations

import pytest

from trendchimp.config.loader import ConfigurationError, load_settings
from trendchimp.config.settings import AlpacaSettings, TrendChimpSettings


def test_env_prefix_and_nested_delimiter(monkeypatch):
    monkeypatch.setenv("TRENDCHIMP_ALPACA__API_KEY", "k")
    monkeypatch.setenv("TRENDCHIMP_ALPACA__SECRET_KEY", "s")
    monkeypatch.setenv("TRENDCHIMP_RISK__MAX_OPEN_POSITIONS", "7")
    settings = TrendChimpSettings(_env_file=None)
    assert settings.alpaca.api_key == "k"
    assert settings.risk.max_open_positions == 7


def test_live_trading_double_lock_blocks_without_confirmation():
    with pytest.raises(ValueError, match="LIVE_TRADING_CONFIRMED"):
        TrendChimpSettings(
            alpaca=AlpacaSettings(api_key="k", secret_key="s", paper=False),
            live_trading_confirmed=False,
        )


def test_live_trading_allowed_with_both_flags():
    settings = TrendChimpSettings(
        alpaca=AlpacaSettings(api_key="k", secret_key="s", paper=False),
        live_trading_confirmed=True,
    )
    assert settings.alpaca.paper is False
    assert settings.live_trading_confirmed is True


def test_credentials_required():
    with pytest.raises(ValueError, match="credentials are required"):
        TrendChimpSettings(alpaca=AlpacaSettings(api_key="", secret_key=""))


def test_loader_wraps_validation_error(monkeypatch):
    from pydantic import ValidationError

    def _raise():
        try:
            TrendChimpSettings(alpaca=AlpacaSettings(api_key="", secret_key=""))
        except ValidationError as exc:
            raise exc

    monkeypatch.setattr("trendchimp.config.loader.TrendChimpSettings", _raise)
    with pytest.raises(ConfigurationError, match="Invalid trendchimp configuration"):
        load_settings()
