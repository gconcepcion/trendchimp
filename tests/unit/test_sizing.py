from __future__ import annotations

from decimal import Decimal

from trendchimp.risk.sizing import TurtleUnitSizer


def _sizer(max_position_pct=0.20):
    return TurtleUnitSizer(risk_per_trade_pct=0.01, atr_stop_mult=2.0,
                           max_position_pct=max_position_pct)


def test_unit_formula_whole_shares():
    # equity 100k, risk 1% = $1000; 2N stop = $4 -> 250 shares; caps don't bind at $50.
    qty = _sizer().size(Decimal("50"), Decimal("2"), Decimal("100000"), Decimal("100000"))
    assert qty == 250


def test_capped_by_max_position_pct():
    # At $100/share, max_position_pct 20% of 100k = $20k -> 200 shares caps the 250.
    qty = _sizer().size(Decimal("100"), Decimal("2"), Decimal("100000"), Decimal("100000"))
    assert qty == 200


def test_capped_by_buying_power():
    qty = _sizer().size(Decimal("50"), Decimal("2"), Decimal("100000"), Decimal("1000"))
    # 95% of $1000 / $50 = 19 shares
    assert qty == 19


def test_zero_atr_returns_zero():
    assert _sizer().size(Decimal("100"), Decimal("0"), Decimal("100000"), Decimal("100000")) == 0


def test_zero_equity_returns_zero():
    assert _sizer().size(Decimal("100"), Decimal("2"), Decimal("0"), Decimal("100000")) == 0
