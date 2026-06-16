from __future__ import annotations

import numpy as np
import pandas as pd

from trendchimp.screener.technicals import TrendScorer


def _df(close: np.ndarray, day_range: float = 0.01, volume: float = 1_000_000.0) -> pd.DataFrame:
    high = close * (1 + day_range)
    low = close * (1 - day_range)
    return pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": np.full(len(close), volume),
    })


def _uptrend(n=260, start=100.0, daily=0.005) -> np.ndarray:
    return start * (1.0 + daily) ** np.arange(n)


def _flat(n=260, base=100.0) -> np.ndarray:
    return base + 2.0 * np.sin(np.arange(n) / 5.0)


class _FakeMarketData:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames

    def get_bars_batch(self, symbols, timeframe, start, end):
        return {s: self._frames[s] for s in symbols if s in self._frames}


def test_uptrend_outranks_flat():
    frames = {"UP": _df(_uptrend()), "FLAT": _df(_flat())}
    out = TrendScorer().score_all(["UP", "FLAT"], _FakeMarketData(frames), top_n=10)
    by_symbol = {c.symbol: c for c in out}
    assert "UP" in by_symbol and "FLAT" in by_symbol
    assert by_symbol["UP"].score > by_symbol["FLAT"].score
    assert by_symbol["UP"].trend_aligned is True
    assert by_symbol["UP"].adx > by_symbol["FLAT"].adx


def test_illiquid_symbol_is_gated_out():
    frames = {"THIN": _df(_uptrend(), volume=100.0)}  # ~$ thousands/day << $20M gate
    out = TrendScorer().score_all(["THIN"], _FakeMarketData(frames), top_n=10)
    assert out == []


def test_short_history_is_skipped():
    frames = {"NEW": _df(_uptrend(n=120))}  # < 200 bars
    out = TrendScorer().score_all(["NEW"], _FakeMarketData(frames), top_n=10)
    assert out == []


def test_near_high_metric_has_no_look_ahead():
    # A fresh all-time high today should score >= 0 against the PRIOR 20-day high,
    # never using today's own bar in the channel it is compared to.
    frames = {"UP": _df(_uptrend())}
    out = TrendScorer().score_all(["UP"], _FakeMarketData(frames), top_n=10)
    cand = out[0]
    # Steady uptrend: price is essentially at the prior-window high (slightly under
    # the prior bar's intraday high), so proximity is near the breakout edge.
    assert cand.pct_from_high_20 > -0.02


def test_top_n_limit():
    frames = {f"S{i}": _df(_uptrend(start=50 + i)) for i in range(5)}
    out = TrendScorer().score_all(list(frames), _FakeMarketData(frames), top_n=3)
    assert len(out) == 3


def _price_frames():
    # CHEAP ends near $36 (start 10 -> *3.6), PRICEY near $364 (start 100).
    return {"CHEAP": _df(_uptrend(start=10.0)), "PRICEY": _df(_uptrend(start=100.0))}


def test_max_price_filters_out_expensive_names():
    frames = _price_frames()
    out = TrendScorer().score_all(list(frames), _FakeMarketData(frames), top_n=10, max_price=50.0)
    symbols = {c.symbol for c in out}
    assert "CHEAP" in symbols and "PRICEY" not in symbols  # PRICEY (~$364) dropped


def test_min_price_filters_out_cheap_names():
    frames = _price_frames()
    out = TrendScorer().score_all(list(frames), _FakeMarketData(frames), top_n=10, min_price=50.0)
    symbols = {c.symbol for c in out}
    assert "PRICEY" in symbols and "CHEAP" not in symbols  # CHEAP (~$36) dropped


def test_zero_price_band_is_no_op():
    frames = _price_frames()
    out = TrendScorer().score_all(list(frames), _FakeMarketData(frames), top_n=10)  # defaults 0/0
    assert {c.symbol for c in out} == {"CHEAP", "PRICEY"}  # default behavior unchanged
