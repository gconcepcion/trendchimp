from __future__ import annotations

import json
import os

from trendchimp.config.settings import (
    AlpacaSettings,
    TradingSettings,
    TrendChimpSettings,
)
from trendchimp.runner.bot import TradingBot


def _settings(symbols, universe_file=""):
    return TrendChimpSettings(
        alpaca=AlpacaSettings(api_key="k", secret_key="s", paper=True),
        trading=TradingSettings(symbols=symbols, universe_file=universe_file),
    )


def test_uses_configured_symbols_when_no_universe_file():
    bot = TradingBot(_settings(["aapl", "msft"]))
    assert bot._resolve_symbols() == ["AAPL", "MSFT"]


def test_prefers_universe_file_when_present(tmp_path):
    path = os.path.join(str(tmp_path), "universe.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"symbols": ["nvda", "amd"]}, fh)
    bot = TradingBot(_settings(["AAPL"], universe_file=path))
    assert bot._resolve_symbols() == ["NVDA", "AMD"]


def test_falls_back_when_universe_file_missing(tmp_path):
    path = os.path.join(str(tmp_path), "does_not_exist.json")
    bot = TradingBot(_settings(["AAPL"], universe_file=path))
    assert bot._resolve_symbols() == ["AAPL"]


def test_falls_back_when_universe_file_empty(tmp_path):
    path = os.path.join(str(tmp_path), "empty.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"symbols": []}, fh)
    bot = TradingBot(_settings(["AAPL"], universe_file=path))
    assert bot._resolve_symbols() == ["AAPL"]
