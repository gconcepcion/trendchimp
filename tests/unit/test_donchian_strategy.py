from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import make_bar_sequence
from trendchimp.signals.models import OrderSide, TradeIntent
from trendchimp.strategies.donchian import DonchianBreakoutStrategy


def _params(**over):
    base = {"entry_channel": 3, "exit_channel": 2, "atr_period": 3, "atr_stop_mult": 2.0}
    base.update(over)
    return base


async def _run(strategy, bars):
    return [await strategy.on_bar(bar) for bar in bars]


async def test_no_look_ahead_equal_high_does_not_trigger():
    strat = DonchianBreakoutStrategy(_params())
    # Four identical bars: bar index 3's high equals the prior 3-bar max (10) exactly.
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4)
    out = await _run(strat, bars)
    assert all(sig == [] for sig in out), "equal-to-channel high must not breakout"


async def test_long_entry_on_breakout_with_2n_stop():
    strat = DonchianBreakoutStrategy(_params())
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4 + [(11, 10, 10.5)])
    out = await _run(strat, bars)
    assert out[3] == []
    assert len(out[4]) == 1
    sig = out[4][0]
    assert sig.intent == TradeIntent.ENTER_LONG
    assert sig.side == OrderSide.BUY
    assert sig.metadata["entry_price"] == Decimal("10.5")
    assert sig.metadata["atr"] == Decimal("1")          # constant 1-wide range
    assert sig.metadata["stop_price"] == Decimal("8.5")  # 10.5 - 2*1


async def test_short_entry_on_breakdown_with_2n_stop():
    strat = DonchianBreakoutStrategy(_params())
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4 + [(9, 8, 8.5)])
    out = await _run(strat, bars)
    sig = out[4][0]
    assert sig.intent == TradeIntent.ENTER_SHORT
    assert sig.side == OrderSide.SELL
    assert sig.metadata["stop_price"] == Decimal("10.5")  # 8.5 + 2*1


async def test_allow_short_false_suppresses_short_entry():
    strat = DonchianBreakoutStrategy(_params(allow_short=False))
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4 + [(9, 8, 8.5)])
    out = await _run(strat, bars)
    assert out[4] == []


async def test_long_then_exit_on_opposite_channel_break():
    strat = DonchianBreakoutStrategy(_params())
    bars = make_bar_sequence("AAPL", [
        (10, 9, 9.5), (10, 9, 9.5), (10, 9, 9.5), (10, 9, 9.5),
        (11, 10, 10.5),  # idx 4: long entry
        (11, 10, 10.5),  # idx 5: hold
        (9, 8, 8.5),     # idx 6: low breaks exit channel -> exit long
    ])
    out = await _run(strat, bars)
    assert out[4][0].intent == TradeIntent.ENTER_LONG
    assert out[5] == []
    assert len(out[6]) == 1
    assert out[6][0].intent == TradeIntent.EXIT_LONG
    assert out[6][0].side == OrderSide.SELL


async def test_no_reentry_while_long():
    strat = DonchianBreakoutStrategy(_params())
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4 + [(11, 10, 10.5), (12, 11, 11.5)])
    out = await _run(strat, bars)
    assert out[4][0].intent == TradeIntent.ENTER_LONG
    # idx 5 is a fresh high but we are already long -> no second entry.
    assert out[5] == []


async def test_seed_position_prevents_entry():
    strat = DonchianBreakoutStrategy(_params())
    # Prime channels with flat bars, then seed as already-long.
    prime = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4)
    await _run(strat, prime)
    strat.seed_position("AAPL", qty=10.0)
    # A breakout high now should NOT enter (already long); it may exit only.
    out = await strat.on_bar(make_bar_sequence("AAPL", [(11, 10, 10.5)])[0])
    assert all(s.intent != TradeIntent.ENTER_LONG for s in out)
