from __future__ import annotations

import pytest

from trendchimp.indicators.rolling import DonchianChannel, WilderATR


def test_donchian_not_ready_until_full():
    ch = DonchianChannel(3)
    ch.push(10, 9)
    ch.push(11, 8)
    assert ch.upper() is None
    assert ch.lower() is None
    ch.push(12, 7)
    assert ch.ready
    assert ch.upper() == 12
    assert ch.lower() == 7


def test_donchian_rolls_off_oldest():
    ch = DonchianChannel(2)
    ch.push(10, 9)
    ch.push(11, 8)
    assert (ch.upper(), ch.lower()) == (11, 8)
    ch.push(12, 10)  # drops (10, 9)
    assert (ch.upper(), ch.lower()) == (12, 8)


def test_wilder_atr_seed_is_simple_average():
    atr = WilderATR(2)
    atr.update(10, 8, 9)   # TR = 2 (first bar, no prev close)
    assert atr.current() is None
    atr.update(11, 9, 10)  # TR = 2 -> seed avg = 2.0
    assert atr.current() == pytest.approx(2.0)


def test_wilder_atr_smoothing():
    atr = WilderATR(2)
    atr.update(10, 8, 9)    # TR 2
    atr.update(11, 9, 10)   # TR 2 -> ATR 2.0
    atr.update(14, 10, 12)  # TR max(4, |14-10|, |10-10|) = 4 -> (2*1 + 4)/2 = 3.0
    assert atr.current() == pytest.approx(3.0)
