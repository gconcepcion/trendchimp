from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from alpaca.trading.enums import OrderClass
from alpaca.trading.requests import MarketOrderRequest

from tests.conftest import make_bar, make_position
from trendchimp.orders.manager import OrderManager
from trendchimp.orders.models import OrderDecision
from trendchimp.runner.bot import TradingBot
from trendchimp.signals.models import OrderSide, Signal, TradeIntent


# ------------------------------------------------------------ OTO bracket on submit
def test_submit_with_bracket_attaches_oto_stop(mock_trading_client, portfolio_state):
    mgr = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10, TradeIntent.ENTER_LONG, Decimal("96")),
               attach_stop_bracket=True)
    req = mock_trading_client.submit_order.call_args.args[0]
    assert isinstance(req, MarketOrderRequest)
    assert req.order_class == OrderClass.OTO
    assert req.stop_loss is not None and req.stop_loss.stop_price == 96.0


def test_submit_without_bracket_is_simple(mock_trading_client, portfolio_state):
    mgr = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10, TradeIntent.ENTER_LONG, Decimal("96")))
    req = mock_trading_client.submit_order.call_args.args[0]
    assert req.order_class is None and req.stop_loss is None


# ----------------------------------------------------------------- decision helpers
def test_completed_sessions_drops_todays_bar():
    et_today = datetime.now(tz=timezone.utc)
    yesterday = make_bar("AAPL", close=100, timestamp=et_today - timedelta(days=2))
    today = make_bar("AAPL", close=101, timestamp=et_today)
    completed = TradingBot._completed_sessions([yesterday, today])
    assert completed == [yesterday]  # today's forming bar excluded


def test_has_open_entry_ignores_stops():
    om = SimpleNamespace(get_active_orders=lambda: [
        SimpleNamespace(symbol="AAPL", is_stop_order=True),   # a stop, not an entry
    ])
    assert TradingBot._has_open_entry(om, "AAPL") is False
    om = SimpleNamespace(get_active_orders=lambda: [
        SimpleNamespace(symbol="AAPL", is_stop_order=False),  # an open entry
    ])
    assert TradingBot._has_open_entry(om, "AAPL") is True


# --------------------------------------------------------------- _decide_once wiring
class _FakeStrategy:
    name = "fake"

    def __init__(self, signal):
        self._signal = signal
        self.seeded: list = []
        self.on_bar_calls = 0

    def get_required_history(self):
        return 1

    def seed_position(self, symbol, qty):
        self.seeded.append((symbol, qty))

    async def on_bar(self, bar):
        self.on_bar_calls += 1
        return [self._signal]   # priming bars ignore the return; only the last is acted on


class _FakeOM:
    def __init__(self, active=None):
        self._active = active or []
        self.submits: list = []

    def get_active_orders(self):
        return self._active

    def submit(self, decision, attach_stop_bracket=False):
        self.submits.append((decision, attach_stop_bracket))
        return object()


class _FakeMD:
    def __init__(self, bars):
        self._bars = bars

    def get_bars(self, symbol, timeframe, start, end):
        return self._bars


def _bars(n=4):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)  # all well in the past -> all completed
    return [make_bar("AAPL", close=100 + i, timestamp=base + timedelta(days=i)) for i in range(n)]


def _ctx(strategy, om, decision, position=None):
    return SimpleNamespace(
        strategy=strategy,
        market_data=_FakeMD(_bars()),
        portfolio=SimpleNamespace(get_position=lambda s: position),
        risk_manager=SimpleNamespace(evaluate=lambda sig, pf: decision),
        order_manager=om,
    )


async def test_decide_once_submits_entry_with_bracket(default_settings):
    sig = Signal(symbol="AAPL", side=OrderSide.BUY, strategy_name="fake",
                 timestamp=datetime.now(tz=timezone.utc), intent=TradeIntent.ENTER_LONG, strength=1.0)
    decision = OrderDecision("AAPL", OrderSide.BUY, 10, TradeIntent.ENTER_LONG, Decimal("96"))
    strat = _FakeStrategy(sig)
    om = _FakeOM()
    bot = TradingBot(default_settings)

    entries, exits = await bot._decide_once(_ctx(strat, om, decision), "AAPL")

    assert (entries, exits) == (1, 0)
    assert strat.seeded == [("AAPL", 0.0)]          # aligned to flat position
    assert len(om.submits) == 1
    assert om.submits[0][1] is True                 # attach_stop_bracket on the entry


async def test_decide_once_skips_duplicate_entry(default_settings):
    sig = Signal(symbol="AAPL", side=OrderSide.BUY, strategy_name="fake",
                 timestamp=datetime.now(tz=timezone.utc), intent=TradeIntent.ENTER_LONG, strength=1.0)
    decision = OrderDecision("AAPL", OrderSide.BUY, 10, TradeIntent.ENTER_LONG, Decimal("96"))
    om = _FakeOM(active=[SimpleNamespace(symbol="AAPL", is_stop_order=False)])  # already pending
    bot = TradingBot(default_settings)

    entries, exits = await bot._decide_once(_ctx(_FakeStrategy(sig), om, decision), "AAPL")

    assert (entries, exits) == (0, 0)
    assert om.submits == []


async def test_decide_once_no_signal_no_order(default_settings):
    class _Flat(_FakeStrategy):
        async def on_bar(self, bar):
            return []
    bot = TradingBot(default_settings)
    om = _FakeOM()
    entries, exits = await bot._decide_once(_ctx(_Flat(None), om, None), "AAPL")
    assert (entries, exits) == (0, 0) and om.submits == []
