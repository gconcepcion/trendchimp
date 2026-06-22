from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from tests.conftest import make_position
from trendchimp.config.settings import RiskSettings
from trendchimp.data.models import InternalAccount
from trendchimp.portfolio.state import PortfolioState
from trendchimp.risk.killswitch import KillSwitch
from trendchimp.risk.manager import RiskManager
from trendchimp.risk.sizing import TurtleUnitSizer
from trendchimp.signals.models import OrderSide, Signal, TradeIntent


def _portfolio(positions=(), equity="100000"):
    client = MagicMock()
    client.get_account.return_value = InternalAccount(
        buying_power=Decimal(equity), cash=Decimal(equity),
        portfolio_value=Decimal(equity), equity=Decimal(equity),
    )
    client.get_positions.return_value = list(positions)
    state = PortfolioState()
    state.reconcile(client)
    return state


def _manager(risk=None, killswitch=None):
    risk = risk or RiskSettings()
    sizer = TurtleUnitSizer(risk.risk_per_trade_pct, risk.atr_stop_mult,
                            risk.max_position_pct, risk.max_gross_exposure_pct)
    return RiskManager(risk, sizer, killswitch)


def _entry_signal(intent, side, entry="100", atr="2", stop="96", symbol="AAPL"):
    return Signal(
        symbol=symbol, side=side, strategy_name="donchian_breakout",
        timestamp=datetime.now(tz=timezone.utc), intent=intent,
        metadata={"entry_price": Decimal(entry), "atr": Decimal(atr),
                  "stop_price": Decimal(stop)},
    )


def _exit_signal(intent, side, symbol="AAPL"):
    return Signal(symbol=symbol, side=side, strategy_name="donchian_breakout",
                  timestamp=datetime.now(tz=timezone.utc), intent=intent, metadata={})


def test_enter_long_produces_sized_decision():
    mgr = _manager(killswitch=KillSwitch(0.02, 0.20))
    decision = mgr.evaluate(_entry_signal(TradeIntent.ENTER_LONG, OrderSide.BUY),
                            _portfolio())
    assert decision is not None
    assert decision.side == OrderSide.BUY
    assert decision.intent == TradeIntent.ENTER_LONG
    assert decision.qty == 200  # capped by max_position_pct at $100/share
    assert decision.stop_price == Decimal("96")


def test_enter_long_blocked_when_already_long():
    mgr = _manager()
    pf = _portfolio(positions=[make_position(qty="10")])
    assert mgr.evaluate(_entry_signal(TradeIntent.ENTER_LONG, OrderSide.BUY), pf) is None


def test_enter_short_blocked_when_shorting_disabled():
    mgr = _manager(risk=RiskSettings(allow_short=False))
    decision = mgr.evaluate(
        _entry_signal(TradeIntent.ENTER_SHORT, OrderSide.SELL, stop="104"), _portfolio()
    )
    assert decision is None


def test_max_open_positions_blocks_entry():
    mgr = _manager(risk=RiskSettings(max_open_positions=1))
    pf = _portfolio(positions=[make_position(symbol="MSFT", qty="5")])
    decision = mgr.evaluate(_entry_signal(TradeIntent.ENTER_LONG, OrderSide.BUY), pf)
    assert decision is None


def test_exit_closes_actual_position_qty_and_bypasses_killswitch():
    tripped = KillSwitch(0.0, 1.0)  # daily limit 0 -> trips on first check
    mgr = _manager(killswitch=tripped)
    pf = _portfolio(positions=[make_position(qty="7")])
    decision = mgr.evaluate(_exit_signal(TradeIntent.EXIT_LONG, OrderSide.SELL), pf)
    assert decision is not None
    assert decision.qty == 7
    assert decision.stop_price is None


def test_killswitch_blocks_new_entry():
    tripped = KillSwitch(0.0, 1.0)
    mgr = _manager(killswitch=tripped)
    decision = mgr.evaluate(_entry_signal(TradeIntent.ENTER_LONG, OrderSide.BUY), _portfolio())
    assert decision is None


def test_exit_with_no_position_returns_none():
    mgr = _manager()
    decision = mgr.evaluate(_exit_signal(TradeIntent.EXIT_LONG, OrderSide.SELL), _portfolio())
    assert decision is None
