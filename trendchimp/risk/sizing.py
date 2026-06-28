from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def protective_stop_price(entry_price: Decimal, distance: Decimal, is_long: bool) -> Decimal:
    """Stop price `distance` away from entry, on the protective side: below a long,
    above a short. Shared by entry sizing and startup stop-recovery so a recovered
    stop sits exactly where the strategy would have placed it."""
    raw = entry_price - distance if is_long else entry_price + distance
    return raw.quantize(Decimal("0.01"))


class TurtleUnitSizer:
    """ATR/N-unit position sizing (Turtle "Unit" formula).

    Risk a fixed fraction of equity per trade. The stop sits ``atr_stop_mult``×N
    away, so one Unit loses exactly ``risk_per_trade_pct`` of equity if stopped.
    Returns whole shares (works for shorts, which can't be fractional on Alpaca).
    """

    def __init__(
        self,
        risk_per_trade_pct: float,
        atr_stop_mult: float,
        max_position_pct: float,
        max_gross_exposure_pct: float = 1.0,
    ) -> None:
        self.risk_per_trade_pct = Decimal(str(risk_per_trade_pct))
        self.atr_stop_mult = Decimal(str(atr_stop_mult))
        self.max_position_pct = Decimal(str(max_position_pct))
        self.max_gross_exposure_pct = Decimal(str(max_gross_exposure_pct))

    def size(
        self,
        entry_price: Decimal,
        n: Decimal,
        equity: Decimal,
        buying_power: Decimal,
        gross_exposure: Decimal = Decimal("0"),
    ) -> int:
        stop_distance = self.atr_stop_mult * n
        if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
            return 0

        risk_dollars = equity * self.risk_per_trade_pct
        shares = int(risk_dollars / stop_distance)  # floor toward zero, whole shares

        # Hard caps: single-unit notional vs equity, 95% of buying power, and the
        # aggregate gross-exposure headroom (total open notional vs equity).
        max_by_equity = int((equity * self.max_position_pct) / entry_price)
        max_by_bp = int((buying_power * Decimal("0.95")) / entry_price)
        gross_headroom = (equity * self.max_gross_exposure_pct) - gross_exposure
        max_by_gross = int(gross_headroom / entry_price) if gross_headroom > 0 else 0

        capped = max(0, min(shares, max_by_equity, max_by_bp, max_by_gross))
        if capped < shares:
            logger.debug(
                "Sizing capped: raw=%d -> %d (max_equity=%d, max_bp=%d, max_gross=%d)",
                shares, capped, max_by_equity, max_by_bp, max_by_gross,
            )
        return capped
