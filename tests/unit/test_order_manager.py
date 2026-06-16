from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from alpaca.trading.enums import OrderSide as AlpacaSide
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest

from tests.conftest import make_trade_update
from trendchimp.clients.trading import TradingClientWrapper
from trendchimp.orders.manager import OrderManager
from trendchimp.orders.models import OrderDecision
from trendchimp.signals.models import OrderSide, TradeIntent


def _manager(mock_trading_client, portfolio_state):
    return OrderManager(
        trading_client=mock_trading_client,
        portfolio_state=portfolio_state,
        dry_run=False,
    )


def _last_request(mock_client):
    return mock_client.submit_order.call_args.args[0]


def test_submit_entry_sends_qty_market_order(mock_trading_client, portfolio_state):
    mgr = _manager(mock_trading_client, portfolio_state)
    decision = OrderDecision(symbol="AAPL", side=OrderSide.BUY, qty=10,
                             intent=TradeIntent.ENTER_LONG, stop_price=Decimal("96"))
    managed = mgr.submit(decision)
    assert managed is not None
    req = _last_request(mock_trading_client)
    assert isinstance(req, MarketOrderRequest)
    assert req.side == AlpacaSide.BUY
    assert req.qty == 10


async def test_long_entry_fill_attaches_sell_stop(mock_trading_client, portfolio_state):
    mgr = _manager(mock_trading_client, portfolio_state)
    managed = mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10,
                                       TradeIntent.ENTER_LONG, Decimal("96")))
    update = make_trade_update(order_id=managed.order_id, side="buy", qty="10",
                               price="100", status="filled")
    await mgr.handle_trade_update(update)

    assert mock_trading_client.submit_order.call_count == 2
    stop_req = _last_request(mock_trading_client)
    assert isinstance(stop_req, StopOrderRequest)
    assert stop_req.side == AlpacaSide.SELL
    assert stop_req.stop_price == 96.0
    assert stop_req.qty == 10


async def test_short_entry_fill_attaches_buy_stop(mock_trading_client, portfolio_state):
    mgr = _manager(mock_trading_client, portfolio_state)
    managed = mgr.submit(OrderDecision("AAPL", OrderSide.SELL, 10,
                                       TradeIntent.ENTER_SHORT, Decimal("104")))
    update = make_trade_update(order_id=managed.order_id, side="sell", qty="10",
                               price="100", status="filled")
    await mgr.handle_trade_update(update)

    stop_req = _last_request(mock_trading_client)
    assert isinstance(stop_req, StopOrderRequest)
    assert stop_req.side == AlpacaSide.BUY
    assert stop_req.stop_price == 104.0


async def test_exit_fill_cancels_open_stop(mock_trading_client, portfolio_state):
    mgr = _manager(mock_trading_client, portfolio_state)
    # Enter long and fill -> stop order created (ord-2).
    entry = mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10,
                                     TradeIntent.ENTER_LONG, Decimal("96")))
    await mgr.handle_trade_update(make_trade_update(order_id=entry.order_id, side="buy",
                                                    qty="10", price="100", status="filled"))
    # Now exit and fill -> the stop must be cancelled.
    exit_order = mgr.submit(OrderDecision("AAPL", OrderSide.SELL, 10,
                                          TradeIntent.EXIT_LONG, None))
    await mgr.handle_trade_update(make_trade_update(order_id=exit_order.order_id, side="sell",
                                                    qty="10", price="105", status="filled"))
    mock_trading_client.cancel_order.assert_called_once_with("ord-2")


async def test_fill_with_alpaca_enum_status_attaches_stop(mock_trading_client, portfolio_state):
    """Regression: live trade updates carry a real OrderStatus enum, whose str() is
    'OrderStatus.FILLED' not 'filled'. The fill must still be detected and a
    protective stop attached (previously it silently never was)."""
    from unittest.mock import MagicMock

    from alpaca.trading.enums import OrderStatus

    mgr = _manager(mock_trading_client, portfolio_state)
    managed = mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10,
                                       TradeIntent.ENTER_LONG, Decimal("96")))

    order = MagicMock()
    order.id = managed.order_id
    order.symbol = "AAPL"
    order.side = "buy"
    order.qty = "10"
    order.status = OrderStatus.FILLED        # the real broker type, not "filled"
    order.filled_qty = "10"
    order.filled_avg_price = "100"
    update = MagicMock()
    update.order = order

    await mgr.handle_trade_update(update)

    assert mock_trading_client.submit_order.call_count == 2
    stop_req = _last_request(mock_trading_client)
    assert isinstance(stop_req, StopOrderRequest)
    assert stop_req.side == AlpacaSide.SELL


def test_reconciled_open_order_reads_as_open(mock_trading_client, portfolio_state):
    """Regression: reconcile() ingests broker orders whose status/side/type are
    enums. is_open()/has_protective_stop must work, or recovery double-places stops."""
    from alpaca.trading.enums import OrderSide as RawSide, OrderStatus, OrderType
    from alpaca.trading.requests import StopOrderRequest as _Req  # noqa: F401

    stop_order = MagicMock()
    stop_order.id = "ord-99"
    stop_order.symbol = "AAPL"
    stop_order.side = RawSide.SELL
    stop_order.type = OrderType.STOP
    stop_order.status = OrderStatus.NEW
    stop_order.qty = "10"
    stop_order.stop_price = "96"
    mock_trading_client.get_open_orders.return_value = [stop_order]

    mgr = _manager(mock_trading_client, portfolio_state)
    mgr.reconcile()

    assert mgr.has_protective_stop("AAPL", OrderSide.SELL, 10) is True


def test_dry_run_submits_nothing(mock_trading_client, portfolio_state):
    mgr = OrderManager(mock_trading_client, portfolio_state, dry_run=True)
    result = mgr.submit(OrderDecision("AAPL", OrderSide.BUY, 10,
                                      TradeIntent.ENTER_LONG, Decimal("96")))
    assert result is None
    mock_trading_client.submit_order.assert_not_called()
