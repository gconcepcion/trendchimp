from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from alpaca.trading.enums import OrderSide as AlpacaSide
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest

from tests.conftest import make_bar_sequence, make_position
from trendchimp.config.settings import RiskSettings, TrendChimpSettings, AlpacaSettings
from trendchimp.orders.manager import OrderManager
from trendchimp.runner.recovery import (
    convert_orphans_to_trailing_stops,
    recover_protective_stops,
)
from trendchimp.signals.models import OrderSide


# ----------------------------------------------------------- routine-level tests
class _FakeOM:
    def __init__(self, has_stop=False, has_trailing=False, covered=None):
        self._has = has_stop
        self._has_trailing = has_trailing
        self._covered = covered
        self.placed: list = []
        self.flattened: list = []
        self.trailed: list = []
        self.cancelled: list = []

    def has_protective_stop(self, symbol, side, qty):
        return self.open_stop_qty(symbol, side) >= qty

    def open_stop_qty(self, symbol, side):
        if self._covered is not None:
            return self._covered
        return 9999 if self._has else 0

    def has_trailing_stop(self, symbol, side, qty):
        return self._has_trailing

    def place_protective_stop(self, symbol, side, qty, stop_price):
        self.placed.append((symbol, side, qty, stop_price))
        return object()

    def cancel_open_stops(self, symbol):
        self.cancelled.append(symbol)

    def place_trailing_stop(self, symbol, side, qty, *, trail_percent=None, trail_price=None):
        self.trailed.append((symbol, side, qty, trail_percent, trail_price))
        return object()

    def flatten_now(self, symbol, side, qty):
        self.flattened.append((symbol, side, qty))
        return object()


class _FakePortfolio:
    def __init__(self, positions):
        self._positions = positions

    def get_all_positions(self):
        return self._positions


class _FakeMarketData:
    def __init__(self, bars, latest=None):
        self._bars = bars
        self._latest = latest

    def get_bars(self, symbol, timeframe, start, end):
        return self._bars

    def get_latest_price(self, symbol):
        return self._latest


def _settings():
    return TrendChimpSettings(
        alpaca=AlpacaSettings(api_key="k", secret_key="s", paper=True),
        risk=RiskSettings(atr_stop_mult=2.0, recovery_fallback_stop_pct=0.10),
    )


def _flat_bars(close=100.0, n=20):
    # high-low = 2 constant, flat closes -> Wilder ATR(14) == 2.0 exactly
    return make_bar_sequence("AAPL", [(close + 1, close - 1, close)] * n)


def _run(positions, bars, has_stop=False, latest=None):
    om = _FakeOM(has_stop=has_stop)
    recover_protective_stops(
        om, _FakePortfolio(positions), _FakeMarketData(bars, latest=latest), _settings())
    return om


def test_already_protected_does_nothing():
    om = _run([make_position(qty="10", avg="100")], _flat_bars(), has_stop=True)
    assert om.placed == [] and om.flattened == []


def test_partial_existing_stop_tops_up_only_the_remainder():
    # Position is 10 shares but only 4 are covered by an existing stop -> place a
    # stop for the uncovered 6, never a fresh full-size 10 (which would over-stop and
    # leave a naked reverse position once one fills).
    om = _FakeOM(covered=4)
    recover_protective_stops(
        om, _FakePortfolio([make_position(qty="10", avg="100")]),
        _FakeMarketData(_flat_bars()), _settings())
    assert len(om.placed) == 1
    _, _, qty, _ = om.placed[0]
    assert qty == 6


def test_fully_covered_across_multiple_stops_is_skipped():
    # Two stops of 5 each fully cover a 10-share position -> no single stop is >= 10,
    # but the summed coverage is, so recovery must NOT place a duplicate.
    om = _FakeOM(covered=10)
    recover_protective_stops(
        om, _FakePortfolio([make_position(qty="10", avg="100")]),
        _FakeMarketData(_flat_bars()), _settings())
    assert om.placed == []


def test_long_without_stop_places_sell_stop_at_2n():
    om = _run([make_position(qty="10", avg="100")], _flat_bars())
    assert len(om.placed) == 1
    symbol, side, qty, stop = om.placed[0]
    assert symbol == "AAPL" and side == OrderSide.SELL and qty == 10
    assert stop == Decimal("96.0")  # 100 - 2*ATR(=2)


def test_short_without_stop_places_buy_stop_above():
    om = _run([make_position(qty="-10", avg="100")], _flat_bars())
    symbol, side, qty, stop = om.placed[0]
    assert side == OrderSide.BUY and qty == 10
    assert stop == Decimal("104.0")  # 100 + 2*ATR


def test_breached_long_is_flattened():
    # Entry 100 but price now 90, below the 96 stop -> exit at market, no stop.
    om = _run([make_position(qty="10", avg="100")], _flat_bars(close=90.0))
    assert om.placed == []
    assert om.flattened == [("AAPL", OrderSide.SELL, 10)]


def test_breached_when_market_closed_places_stop_not_market_order():
    # Breached overnight, but the market is closed: a DAY market flatten would just be
    # rejected. Instead rest a protective stop so it resolves at the open.
    om = _FakeOM()
    recover_protective_stops(
        om, _FakePortfolio([make_position(qty="10", avg="100")]),
        _FakeMarketData(_flat_bars(close=90.0)), _settings(), market_open=False)
    assert om.flattened == []
    assert len(om.placed) == 1


def test_breached_when_market_open_still_flattens():
    om = _FakeOM()
    recover_protective_stops(
        om, _FakePortfolio([make_position(qty="10", avg="100")]),
        _FakeMarketData(_flat_bars(close=90.0)), _settings(), market_open=True)
    assert om.flattened == [("AAPL", OrderSide.SELL, 10)]


def test_atr_unavailable_uses_fallback_pct():
    # Too few bars for ATR -> fallback stop at entry * 10% = 90.
    om = _run([make_position(qty="10", avg="100")], _flat_bars(n=5))
    _, side, _, stop = om.placed[0]
    assert side == OrderSide.SELL and stop == Decimal("90.0")


def test_no_positions_is_noop():
    om = _run([], _flat_bars())
    assert om.placed == [] and om.flattened == []


def test_live_price_gap_triggers_flatten():
    # The prior daily close (100) is above the 96 stop, but the live price has
    # gapped down to 90 overnight -> the stop is already breached, so flatten at
    # market instead of placing a stop that would trigger instantly.
    om = _run([make_position(qty="10", avg="100")], _flat_bars(close=100.0), latest=Decimal("90"))
    assert om.placed == []
    assert om.flattened == [("AAPL", OrderSide.SELL, 10)]


def test_failed_emergency_flatten_is_escalated(caplog):
    """A breached position whose flatten submission fails is unprotected AND still
    open — it must be escalated loudly, not silently counted as flattened."""
    import logging

    class _FailingFlattenOM(_FakeOM):
        def flatten_now(self, symbol, side, qty):
            return None  # simulate a rejected/failed emergency exit

    om = _FailingFlattenOM()
    with caplog.at_level(logging.CRITICAL):
        # close 90 is below the 96 stop -> breached -> flatten path.
        recover_protective_stops(
            om, _FakePortfolio([make_position(qty="10", avg="100")]),
            _FakeMarketData(_flat_bars(close=90.0)), _settings())
    assert any("unprotected" in r.message.lower() for r in caplog.records)


def test_failed_stop_submission_is_escalated(caplog):
    import logging

    class _FailingOM(_FakeOM):
        def place_protective_stop(self, symbol, side, qty, stop_price):
            return None  # simulate a rejected/failed stop submission

    om = _FailingOM()
    with caplog.at_level(logging.CRITICAL):
        recover_protective_stops(
            om, _FakePortfolio([make_position(qty="10", avg="100")]),
            _FakeMarketData(_flat_bars()), _settings())
    assert any("unprotected" in r.message.lower() for r in caplog.records)


def test_failed_recovery_drives_safety_halt():
    from trendchimp.safety import SafetyController

    class _FailingOM(_FakeOM):
        def place_protective_stop(self, symbol, side, qty, stop_price):
            return None

    safety = SafetyController(halt_on_unprotected=True)
    recover_protective_stops(
        _FailingOM(), _FakePortfolio([make_position(qty="10", avg="100")]),
        _FakeMarketData(_flat_bars()), _settings(), safety=safety)
    assert safety.halt_entries is True


# ----------------------------------------------------------- orphan trailing-stop
def _run_orphans(positions, universe, bars=None, has_trailing=False, dry_run=False):
    om = _FakeOM(has_trailing=has_trailing)
    md = _FakeMarketData(bars if bars is not None else _flat_bars())
    convert_orphans_to_trailing_stops(
        om, _FakePortfolio(positions), set(universe), md, _settings(), dry_run=dry_run)
    return om


def test_orphan_dropped_from_universe_gets_atr_trailing_stop():
    # AAPL is held but no longer in the universe -> cancel its static stop and place a
    # trailing stop at atr_mult × N. Flat bars give ATR=2.0, mult=2.0 -> trail_price 4.0.
    om = _run_orphans([make_position(symbol="AAPL", qty="10", avg="100")], universe=["MSFT"])
    assert om.cancelled == ["AAPL"]
    assert om.trailed == [("AAPL", OrderSide.SELL, 10, None, Decimal("4.00"))]


def test_short_orphan_gets_buy_trailing_stop():
    om = _run_orphans([make_position(symbol="AAPL", qty="-10", avg="100")], universe=["MSFT"])
    assert om.trailed == [("AAPL", OrderSide.BUY, 10, None, Decimal("4.00"))]


def test_orphan_without_atr_falls_back_to_percent():
    # Too few bars for ATR -> fall back to the flat 5% trailing stop.
    om = _run_orphans([make_position(symbol="AAPL", qty="10", avg="100")],
                      universe=["MSFT"], bars=_flat_bars(n=5))
    assert om.trailed == [("AAPL", OrderSide.SELL, 10, 5.0, None)]


def test_position_still_in_universe_is_untouched():
    om = _run_orphans([make_position(symbol="AAPL", qty="10", avg="100")], universe=["AAPL"])
    assert om.trailed == [] and om.cancelled == []


def test_orphan_trailing_failure_keeps_static_stop(caplog):
    """If the trailing stop can't be placed, the existing static protective stop must
    NOT be cancelled — a brief double-stop is safe, a brief no-stop is not."""
    import logging

    class _FailingTrailOM(_FakeOM):
        def place_trailing_stop(self, symbol, side, qty, *, trail_percent=None, trail_price=None):
            return None  # simulate a rejected trailing-stop submission

    om = _FailingTrailOM()
    with caplog.at_level(logging.ERROR):
        convert_orphans_to_trailing_stops(
            om, _FakePortfolio([make_position(symbol="AAPL", qty="10", avg="100")]),
            {"MSFT"}, _FakeMarketData(_flat_bars()), _settings())
    # Placement failed, so the static stop is left in place (never cancelled).
    assert om.cancelled == []


def test_orphan_already_trailing_is_skipped():
    om = _run_orphans([make_position(symbol="AAPL", qty="10", avg="100")],
                      universe=["MSFT"], has_trailing=True)
    assert om.trailed == [] and om.cancelled == []


# --------------------------------------------------------- OrderManager-level tests
def _alpaca_stop(symbol="AAPL", side="sell", qty="10", stop_price="96", oid="s1"):
    o = MagicMock()
    o.id = oid
    o.symbol = symbol
    o.side = side
    o.qty = qty
    o.status = "new"
    o.type = "stop"
    o.stop_price = stop_price
    o.submitted_at = None
    return o


def test_reconcile_flags_existing_stop(mock_trading_client, portfolio_state):
    mock_trading_client.get_open_orders.return_value = [_alpaca_stop()]
    om = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    om.reconcile()
    assert om.has_protective_stop("AAPL", OrderSide.SELL, 10) is True
    assert om.has_protective_stop("AAPL", OrderSide.BUY, 10) is False  # wrong side


def test_place_protective_stop_submits_gtc(mock_trading_client, portfolio_state):
    om = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    om.place_protective_stop("AAPL", OrderSide.SELL, 10, Decimal("96"))
    req = mock_trading_client.submit_order.call_args.args[0]
    assert isinstance(req, StopOrderRequest)
    assert req.side == AlpacaSide.SELL and req.stop_price == 96.0 and req.qty == 10
    # Now tracked, so a second recovery pass sees it as protected.
    assert om.has_protective_stop("AAPL", OrderSide.SELL, 10) is True


def test_place_trailing_stop_by_price_submits_gtc(mock_trading_client, portfolio_state):
    from alpaca.trading.requests import TrailingStopOrderRequest

    om = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    om.place_trailing_stop("AAPL", OrderSide.SELL, 10, trail_price=Decimal("4"))
    req = mock_trading_client.submit_order.call_args.args[0]
    assert isinstance(req, TrailingStopOrderRequest)
    assert req.side == AlpacaSide.SELL and req.trail_price == 4.0 and req.qty == 10
    # Tracked as a trailing stop, so a later restart sees it and skips re-converting.
    assert om.has_trailing_stop("AAPL", OrderSide.SELL, 10) is True


def test_place_trailing_stop_by_percent_submits_gtc(mock_trading_client, portfolio_state):
    from alpaca.trading.requests import TrailingStopOrderRequest

    om = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    om.place_trailing_stop("AAPL", OrderSide.SELL, 10, trail_percent=5.0)
    req = mock_trading_client.submit_order.call_args.args[0]
    assert isinstance(req, TrailingStopOrderRequest)
    assert req.trail_percent == 5.0 and req.trail_price is None


def test_flatten_now_submits_market(mock_trading_client, portfolio_state):
    om = OrderManager(mock_trading_client, portfolio_state, dry_run=False)
    om.flatten_now("AAPL", OrderSide.SELL, 10)
    req = mock_trading_client.submit_order.call_args.args[0]
    assert isinstance(req, MarketOrderRequest)
    assert req.side == AlpacaSide.SELL and req.qty == 10


def test_dry_run_places_nothing(mock_trading_client, portfolio_state):
    om = OrderManager(mock_trading_client, portfolio_state, dry_run=True)
    assert om.place_protective_stop("AAPL", OrderSide.SELL, 10, Decimal("96")) is None
    assert om.place_trailing_stop("AAPL", OrderSide.SELL, 10, trail_percent=5.0) is None
    assert om.flatten_now("AAPL", OrderSide.SELL, 10) is None
    mock_trading_client.submit_order.assert_not_called()
