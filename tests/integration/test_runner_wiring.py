from __future__ import annotations

from tests.conftest import make_bar_sequence
from trendchimp.config.settings import RiskSettings
from trendchimp.orders.manager import OrderManager
from trendchimp.risk.killswitch import KillSwitch
from trendchimp.risk.manager import RiskManager
from trendchimp.risk.sizing import TurtleUnitSizer
from trendchimp.strategies.donchian import DonchianBreakoutStrategy


def _wire(mock_trading_client, portfolio_state):
    risk = RiskSettings()
    sizer = TurtleUnitSizer(risk.risk_per_trade_pct, risk.atr_stop_mult, risk.max_position_pct)
    killswitch = KillSwitch(risk.daily_loss_limit_pct, risk.max_drawdown_pct)
    risk_manager = RiskManager(risk, sizer, killswitch)
    order_manager = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    strategy = DonchianBreakoutStrategy(
        {"entry_channel": 3, "exit_channel": 2, "atr_period": 3, "atr_stop_mult": 2.0}
    )

    async def on_bar(bar):
        portfolio_state.update_mark(bar.symbol, bar.close)
        if killswitch.check(portfolio_state):
            return
        for sig in await strategy.on_bar(bar):
            decision = risk_manager.evaluate(sig, portfolio_state)
            if decision:
                order_manager.submit(decision)

    return on_bar


async def test_breakout_flows_through_to_order_submission(mock_trading_client, portfolio_state):
    on_bar = _wire(mock_trading_client, portfolio_state)
    bars = make_bar_sequence("AAPL", [(10, 9, 9.5)] * 4 + [(11, 10, 10.5)])
    for bar in bars:
        await on_bar(bar)
    # The breakout on the final bar should have produced exactly one entry order.
    assert mock_trading_client.submit_order.call_count == 1


async def test_no_breakout_submits_nothing(mock_trading_client, portfolio_state):
    on_bar = _wire(mock_trading_client, portfolio_state)
    for bar in make_bar_sequence("AAPL", [(10, 9, 9.5)] * 6):
        await on_bar(bar)
    mock_trading_client.submit_order.assert_not_called()
