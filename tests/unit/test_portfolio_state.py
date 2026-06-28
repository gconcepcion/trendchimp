from __future__ import annotations

from decimal import Decimal

from tests.conftest import make_trade_update


async def test_long_open_and_close_realizes_pnl(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="10", price="100"))
    pos = portfolio_state.get_position("AAPL")
    assert pos.qty == Decimal("10")
    assert pos.is_long

    await portfolio_state.on_fill(make_trade_update(side="sell", qty="10", price="110"))
    assert portfolio_state.get_position("AAPL") is None
    # Realized $10/share * 10 = $100.
    assert portfolio_state.get_daily_pnl() == Decimal("100")


async def test_short_open_and_cover_realizes_pnl(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(side="sell", qty="10", price="100"))
    pos = portfolio_state.get_position("AAPL")
    assert pos.qty == Decimal("-10")
    assert pos.is_short

    # Cover lower -> profit on a short.
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="10", price="90"))
    assert portfolio_state.get_position("AAPL") is None
    assert portfolio_state.get_daily_pnl() == Decimal("100")


async def test_short_unrealized_pnl_via_mark(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(side="sell", qty="10", price="100"))
    portfolio_state.update_mark("AAPL", 90)
    # Short gains $10/share as price falls.
    assert portfolio_state.get_unrealized_pnl() == Decimal("100")


async def test_partial_reduce_keeps_entry_price(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="10", price="100"))
    await portfolio_state.on_fill(make_trade_update(side="sell", qty="4", price="120"))
    pos = portfolio_state.get_position("AAPL")
    assert pos.qty == Decimal("6")
    assert pos.avg_entry_price == Decimal("100")
    # Mark the remaining 6 shares back to entry so unrealized is 0, isolating the
    # realized partial P&L: (120-100)*4 = 80.
    portfolio_state.update_mark("AAPL", 100)
    assert portfolio_state.get_daily_pnl() == Decimal("80")


async def test_gross_exposure_sums_longs_and_abs_shorts_at_marks(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(symbol="AAPL", side="buy", qty="10", price="100"))
    await portfolio_state.on_fill(make_trade_update(symbol="TSLA", side="sell", qty="5", price="200"))
    # Mark moves: long at 110, short at 180. Gross = |10|*110 + |-5|*180 = 2000.
    portfolio_state.update_mark("AAPL", 110)
    portfolio_state.update_mark("TSLA", 180)
    assert portfolio_state.get_gross_exposure() == Decimal("2000")


async def test_peak_equity_only_rises(portfolio_state):
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="100", price="100"))
    portfolio_state.update_mark("AAPL", 110)  # +$1000
    peak = portfolio_state.get_peak_equity()
    assert peak == Decimal("101000")
    portfolio_state.update_mark("AAPL", 90)   # -$1000 vs entry
    assert portfolio_state.get_peak_equity() == peak  # peak does not fall
