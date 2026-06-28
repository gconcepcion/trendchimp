from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendchimp.config.settings import (
    AlpacaSettings,
    RiskSettings,
    TrendChimpSettings,
)
from trendchimp.runner.bot import TradingBot

_T0 = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)


def _bot(watchdog_minutes: int) -> TradingBot:
    settings = TrendChimpSettings(
        alpaca=AlpacaSettings(api_key="k", secret_key="s", paper=True),
        risk=RiskSettings(stop_watchdog_minutes=watchdog_minutes),
    )
    return TradingBot(settings)


def test_watchdog_throttles_reassertion():
    bot = _bot(5)
    calls: list = []
    bot._apply_startup_stops = lambda c: calls.append(1)  # type: ignore[method-assign]
    c = object()

    bot._maybe_run_watchdog(c, now=_T0)                          # first -> runs
    bot._maybe_run_watchdog(c, now=_T0 + timedelta(minutes=2))   # within window -> skip
    bot._maybe_run_watchdog(c, now=_T0 + timedelta(minutes=6))   # past window -> runs
    assert len(calls) == 2


def test_watchdog_disabled_when_interval_zero():
    bot = _bot(0)
    calls: list = []
    bot._apply_startup_stops = lambda c: calls.append(1)  # type: ignore[method-assign]
    bot._maybe_run_watchdog(object(), now=_T0)
    assert calls == []
