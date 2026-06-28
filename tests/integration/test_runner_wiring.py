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
    sizer = TurtleUnitSizer(risk.risk_per_trade_pct, risk.atr_stop_mult,
                            risk.max_position_pct, risk.max_gross_exposure_pct)
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


async def test_unprotected_position_halts_subsequent_entries(mock_trading_client, portfolio_state):
    """End-to-end wiring: when the order manager can't protect a fill it drives the
    shared SafetyController, and the risk manager then blocks all new entries — the
    same SafetyController instance is shared by both, exactly as bot._assemble wires
    them."""
    from decimal import Decimal

    from trendchimp.orders.models import OrderDecision
    from trendchimp.safety import SafetyController
    from trendchimp.signals.models import OrderSide, Signal, TradeIntent
    from tests.conftest import make_trade_update

    risk = RiskSettings()
    safety = SafetyController(halt_on_unprotected=True)
    sizer = TurtleUnitSizer(risk.risk_per_trade_pct, risk.atr_stop_mult,
                            risk.max_position_pct, risk.max_gross_exposure_pct)
    risk_manager = RiskManager(risk, sizer, KillSwitch(risk.daily_loss_limit_pct,
                                                       risk.max_drawdown_pct), safety=safety)
    order_manager = OrderManager(mock_trading_client, portfolio_state, dry_run=False, safety=safety)

    # An entry fills, but the protective stop submission fails -> escalation -> halt.
    entry = order_manager.submit(OrderDecision("AAPL", OrderSide.BUY, 10,
                                               TradeIntent.ENTER_LONG, Decimal("96")))
    mock_trading_client.submit_order.side_effect = RuntimeError("rejected")
    await order_manager.handle_trade_update(
        make_trade_update(order_id=entry.order_id, side="buy", qty="10",
                          price="100", status="filled"))

    # A fresh entry signal for another symbol must now be blocked by the halt latch.
    from datetime import datetime, timezone
    sig = Signal(symbol="MSFT", side=OrderSide.BUY, strategy_name="donchian_breakout",
                 timestamp=datetime.now(tz=timezone.utc), intent=TradeIntent.ENTER_LONG,
                 metadata={"entry_price": Decimal("50"), "atr": Decimal("1"),
                           "stop_price": Decimal("48")})
    assert risk_manager.evaluate(sig, portfolio_state) is None
