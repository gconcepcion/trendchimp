from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from trendchimp.data.models import Bar

logger = logging.getLogger(__name__)

# US equities regular trading hours, used to bucket intraday bars into sessions.
_MARKET_TZ = ZoneInfo("America/New_York")
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
# If the first bar we see for a session arrives more than this past the open, the
# session is being accumulated from the middle (e.g. a mid-session restart), so
# its OHLC would be truncated — discard it rather than emit a malformed bar. Set
# wide enough to tolerate a normal/illiquid late first print, narrow enough to
# catch a real mid-session restart.
_OPEN_GRACE = timedelta(hours=1)


@dataclass
class _DayAccum:
    """Open/high/low/close/volume being accumulated for one symbol's session."""

    session: datetime  # midnight of the session date, in market tz
    open: float
    high: float
    low: float
    close: float
    volume: float
    partial: bool = False  # started mid-session (incomplete) -> do not emit

    def update(self, bar: Bar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.volume += bar.volume


class DailyBarAggregator:
    """Rolls intraday (1-minute) bars up into completed daily OHLCV bars.

    The live websocket only delivers 1-minute bars, but the Turtle strategy is a
    daily-bar system. This buffers minute bars per symbol and emits a completed
    daily Bar the moment a bar arrives for a *later* session than the one being
    accumulated (i.e. the prior day has closed). Decisions therefore land at the
    next session's open, matching classic next-bar Turtle entry.

    By default only regular-trading-hours bars are aggregated so the daily OHLC
    matches the official daily bar; pre/post-market prints are ignored.
    """

    def __init__(self, regular_hours_only: bool = True) -> None:
        self._regular_hours_only = regular_hours_only
        self._accum: dict[str, _DayAccum] = {}

    def add(self, bar: Bar) -> Bar | None:
        """Ingest one intraday bar; return a completed daily Bar when a session
        rolls over, else None."""
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local = ts.astimezone(_MARKET_TZ)

        if self._regular_hours_only and not (_RTH_OPEN <= local.time() < _RTH_CLOSE):
            return None

        session = local.replace(hour=0, minute=0, second=0, microsecond=0)
        # A session opened from a bar well after 9:30 is incomplete (missing the
        # morning) — only meaningful in RTH mode where 9:30 is the true open.
        session_open = local.replace(hour=9, minute=30, second=0, microsecond=0)
        partial = self._regular_hours_only and (local - session_open) > _OPEN_GRACE
        acc = self._accum.get(bar.symbol)

        if acc is None:
            self._accum[bar.symbol] = self._new_accum(session, bar, partial)
            return None

        if session > acc.session:
            was_partial = acc.partial
            self._accum[bar.symbol] = self._new_accum(session, bar, partial)
            if was_partial:
                logger.info(
                    "Discarding partial session for %s (started mid-session, OHLC truncated)",
                    bar.symbol,
                )
                return None
            completed = self._to_bar(bar.symbol, acc)
            logger.info(
                "Daily bar complete for %s: O=%.4f H=%.4f L=%.4f C=%.4f V=%.0f",
                bar.symbol, completed.open, completed.high, completed.low,
                completed.close, completed.volume,
            )
            return completed

        acc.update(bar)
        return None

    @staticmethod
    def _new_accum(session: datetime, bar: Bar, partial: bool = False) -> _DayAccum:
        return _DayAccum(session, bar.open, bar.high, bar.low, bar.close, bar.volume, partial)

    @staticmethod
    def _to_bar(symbol: str, acc: _DayAccum) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=acc.session,
            open=acc.open,
            high=acc.high,
            low=acc.low,
            close=acc.close,
            volume=acc.volume,
        )
