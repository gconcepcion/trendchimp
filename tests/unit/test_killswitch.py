from __future__ import annotations

from tests.conftest import make_trade_update
from trendchimp.risk.killswitch import KillSwitch


async def test_daily_loss_limit_trips_and_latches(portfolio_state):
    ks = KillSwitch(daily_loss_limit_pct=0.02, max_drawdown_pct=0.50)
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="100", price="100"))

    # Mark down to a -$2000 unrealized loss (> 2% of ~$98k equity).
    portfolio_state.update_mark("AAPL", 80)
    assert ks.check(portfolio_state) is True
    assert ks.reason == "daily_loss_limit"

    # Latches: recovery does not un-trip it.
    portfolio_state.update_mark("AAPL", 100)
    assert ks.check(portfolio_state) is True


async def test_drawdown_trips(portfolio_state):
    ks = KillSwitch(daily_loss_limit_pct=0.99, max_drawdown_pct=0.10)
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="1000", price="100"))
    # Peak ~100k; mark down 10% of equity -> drawdown >= 10%.
    portfolio_state.update_mark("AAPL", 90)
    assert ks.check(portfolio_state) is True
    assert ks.reason == "max_drawdown"


async def test_healthy_portfolio_does_not_trip(portfolio_state):
    ks = KillSwitch(daily_loss_limit_pct=0.02, max_drawdown_pct=0.20)
    await portfolio_state.on_fill(make_trade_update(side="buy", qty="100", price="100"))
    portfolio_state.update_mark("AAPL", 101)  # small gain
    assert ks.check(portfolio_state) is False
