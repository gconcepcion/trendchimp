from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import numpy as np

from trendchimp.screener.models import BreakoutCandidate

if TYPE_CHECKING:
    from trendchimp.clients.market_data import MarketDataClient

logger = logging.getLogger(__name__)

_MIN_BARS = 200          # need SMA200
_BATCH = 50              # symbols per Alpaca request
_MIN_DOLLAR_VOLUME = 20_000_000.0   # liquidity gate
_MIN_ATR_PCT = 0.01      # drop dead names
_MAX_ATR_PCT = 0.15      # drop parabolic names


# --------------------------------------------------------------------- indicators
def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan)
    if n < period:
        return out
    out[period - 1] = values[:period].sum()
    for i in range(period, n):
        out[i] = out[i - 1] - out[i - 1] / period + values[i]
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    return np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    tr = _true_range(high, low, close)
    if len(tr) < period:
        return float("nan")
    atr = float(tr[:period].mean())
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
    return atr


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(close) < 2 * period + 1:
        return float("nan")
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(high, low, close)

    tr_s = _wilder_smooth(tr, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    dx = dx[~np.isnan(dx)]
    if len(dx) < period:
        return float("nan")
    adx = float(dx[:period].mean())
    for i in range(period, len(dx)):
        adx = (adx * (period - 1) + dx[i]) / period
    return adx


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


class TrendScorer:
    """Scores symbols for breakout / trend-following fitness (NOT mean reversion)."""

    def score_all(
        self,
        symbols: list[str],
        market_data: "MarketDataClient",
        lookback_days: int = 260,
        top_n: int = 30,
    ) -> list[BreakoutCandidate]:
        end = datetime.now(tz=timezone.utc)
        # Generous calendar window to cover `lookback_days` trading days.
        start = end - timedelta(days=int(lookback_days * 1.5) + 40)

        candidates: list[BreakoutCandidate] = []
        for i in range(0, len(symbols), _BATCH):
            batch = symbols[i:i + _BATCH]
            try:
                frames = market_data.get_bars_batch(batch, "1Day", start, end)
            except Exception:
                logger.exception("Batch fetch failed for %s — skipping", batch[:3])
                continue
            for symbol, df in frames.items():
                cand = self._score_one(symbol, df)
                if cand is not None:
                    candidates.append(cand)

        candidates.sort(key=lambda c: c.score, reverse=True)
        logger.info("Scored %d symbols, returning top %d", len(candidates), top_n)
        return candidates[:top_n]

    def _score_one(self, symbol: str, df) -> BreakoutCandidate | None:
        if df is None or len(df) < _MIN_BARS:
            return None
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)

        price = float(close[-1])
        if price <= 0:
            return None

        sma50 = float(close[-50:].mean())
        sma50_prev = float(close[-51:-1].mean())
        sma200 = float(close[-200:].mean())

        # Prior-window highs (exclude today) so breaking above today scores >= 0.
        high20 = float(high[-21:-1].max())
        high55 = float(high[-56:-1].max())
        pct_from_high_20 = price / high20 - 1.0 if high20 > 0 else -1.0
        pct_from_high_55 = price / high55 - 1.0 if high55 > 0 else -1.0

        pct_above_sma200 = (price - sma200) / sma200 if sma200 > 0 else 0.0
        trend_aligned = price > sma50 > sma200
        adx = compute_adx(high, low, close, 14)
        atr = compute_atr(high, low, close, 14)
        atr_pct = atr / price if (atr == atr and price > 0) else float("nan")
        momentum_3m = price / float(close[-63]) - 1.0 if len(close) >= 63 else 0.0
        dollar_volume = float((close[-20:] * volume[-20:]).mean())

        # Hard gates.
        if dollar_volume < _MIN_DOLLAR_VOLUME:
            return None
        if not (atr_pct == atr_pct) or not (_MIN_ATR_PCT <= atr_pct <= _MAX_ATR_PCT):
            return None

        score = self._composite(
            pct_from_high_20, pct_from_high_55, price, sma50, sma50_prev, sma200,
            pct_above_sma200, adx, momentum_3m,
        )

        return BreakoutCandidate(
            symbol=symbol,
            price=round(price, 2),
            score=round(score, 1),
            pct_from_high_20=round(pct_from_high_20, 4),
            pct_from_high_55=round(pct_from_high_55, 4),
            pct_above_sma200=round(pct_above_sma200, 4),
            trend_aligned=trend_aligned,
            adx=round(adx, 1) if adx == adx else 0.0,
            momentum_3m=round(momentum_3m, 4),
            atr_pct=round(atr_pct, 4),
            dollar_volume=round(dollar_volume, 0),
        )

    def _composite(
        self, pct_high20, pct_high55, price, sma50, sma50_prev, sma200,
        pct_above_sma200, adx, momentum_3m,
    ) -> float:
        # Proximity to the 20-day breakout edge: -10% -> 0, at/above high -> 100.
        proximity = _clip((pct_high20 + 0.10) / 0.10 * 100.0)

        # Trend alignment ladder, 0..100.
        trend = 0.0
        if price > sma200:
            trend += 40.0
        if sma50 > sma200:
            trend += 30.0
        if price > sma50:
            trend += 20.0
        if sma50 > sma50_prev:
            trend += 10.0

        adx_score = _clip((adx - 20.0) / 20.0 * 100.0) if adx == adx else 0.0
        momentum_score = _clip(momentum_3m / 0.30 * 100.0)

        return (
            proximity * 0.35
            + trend * 0.30
            + adx_score * 0.20
            + momentum_score * 0.15
        )
