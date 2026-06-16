from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trendchimp.data.aggregator import DailyBarAggregator
from trendchimp.data.models import Bar

ET = ZoneInfo("America/New_York")


def _bar(sym, y, mo, d, h, mi, o, hi, lo, c, v, tz=ET):
    return Bar(
        symbol=sym,
        timestamp=datetime(y, mo, d, h, mi, tzinfo=tz),
        open=o, high=hi, low=lo, close=c, volume=v,
    )


def test_no_emit_within_same_session():
    agg = DailyBarAggregator()
    assert agg.add(_bar("X", 2026, 6, 11, 9, 30, 10, 11, 9, 10, 100)) is None
    assert agg.add(_bar("X", 2026, 6, 11, 15, 59, 12, 13, 11, 12, 50)) is None


def test_emits_completed_daily_bar_on_session_rollover():
    agg = DailyBarAggregator()
    agg.add(_bar("X", 2026, 6, 11, 9, 30, 10, 11, 9, 10, 100))
    agg.add(_bar("X", 2026, 6, 11, 15, 59, 12, 13, 11, 12, 50))
    done = agg.add(_bar("X", 2026, 6, 12, 9, 30, 14, 14, 13, 13, 20))
    assert done is not None
    # OHLCV aggregated across day 1's bars (open=first, high/low=extremes,
    # close=last, volume=sum).
    assert (done.open, done.high, done.low, done.close, done.volume) == (10, 13, 9, 12, 150)


def test_ignores_pre_and_post_market_bars():
    agg = DailyBarAggregator()
    # Pre-market and post-market prints must not start or pollute a session.
    assert agg.add(_bar("X", 2026, 6, 12, 8, 0, 99, 99, 99, 99, 1)) is None
    agg.add(_bar("X", 2026, 6, 12, 9, 30, 10, 11, 9, 10, 100))
    assert agg.add(_bar("X", 2026, 6, 12, 17, 0, 200, 200, 200, 200, 1)) is None
    done = agg.add(_bar("X", 2026, 6, 13, 9, 30, 11, 11, 10, 11, 5))
    assert (done.high, done.low) == (11, 9)  # 99 and 200 excluded


def test_symbols_are_tracked_independently():
    agg = DailyBarAggregator()
    agg.add(_bar("A", 2026, 6, 11, 10, 0, 1, 1, 1, 1, 1))
    agg.add(_bar("B", 2026, 6, 11, 10, 0, 2, 2, 2, 2, 2))
    assert agg.add(_bar("A", 2026, 6, 12, 10, 0, 3, 3, 3, 3, 3)).symbol == "A"


def test_discards_session_started_mid_day():
    # Simulate a mid-session restart: first bar arrives at 1pm ET. That session's
    # OHLC is truncated (missing the morning), so it must NOT be emitted on
    # rollover; the next full session is emitted normally.
    agg = DailyBarAggregator()
    agg.add(_bar("X", 2026, 6, 11, 13, 0, 10, 11, 9, 10, 100))   # partial day
    agg.add(_bar("X", 2026, 6, 11, 15, 59, 12, 13, 11, 12, 50))
    discarded = agg.add(_bar("X", 2026, 6, 12, 9, 30, 14, 14, 13, 13, 20))
    assert discarded is None                                      # truncated -> dropped
    agg.add(_bar("X", 2026, 6, 12, 15, 59, 15, 16, 14, 15, 30))
    done = agg.add(_bar("X", 2026, 6, 13, 9, 30, 17, 17, 16, 16, 10))
    assert done is not None and (done.open, done.high, done.low) == (14, 16, 13)


def test_naive_timestamps_treated_as_utc():
    # A naive timestamp should be assumed UTC, then converted to ET. 14:00 UTC
    # is 10:00 ET (within RTH), so it must start a session without error.
    agg = DailyBarAggregator()
    assert agg.add(_bar("X", 2026, 6, 11, 14, 0, 10, 10, 10, 10, 1, tz=None)) is None
    done = agg.add(_bar("X", 2026, 6, 12, 14, 0, 11, 11, 11, 11, 1, tz=None))
    assert done is not None and done.close == 10
