from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from trendchimp.signals.models import OrderSide, Signal, TradeIntent


@dataclass
class OrderDecision:
    """Approved, sized order produced by RiskManager.

    Uses whole-share ``qty`` (not notional) because Alpaca short sales cannot be
    fractional/notional.
    """

    symbol: str
    side: OrderSide
    qty: int
    intent: TradeIntent
    stop_price: Decimal | None = None  # 2N protective stop attached on entry fill
    originating_signal: Signal | None = None


@dataclass
class ManagedOrder:
    """Wraps an alpaca-py Order with internal lifecycle tracking."""

    order_id: str
    symbol: str
    side: OrderSide
    qty: Decimal
    status: str
    submitted_at: datetime
    strategy_name: str
    intent: TradeIntent | None = None
    signal: Signal | None = None
    filled_at: datetime | None = None
    filled_avg_price: Decimal | None = None
    filled_qty: Decimal | None = None
    # Stop lifecycle tracking
    is_stop_order: bool = False        # True for protective stop orders (incl. trailing)
    is_trailing: bool = False          # True for broker trailing-stop orders
    stop_price: Decimal | None = None  # intended 2N stop price (entries) / actual (stops)
    entry_price: Decimal | None = None
    stop_order_id: str | None = None   # ID of the attached protective stop
    raw: Any = field(default=None, repr=False)

    def is_open(self) -> bool:
        # "held" covers an OTO/bracket child stop leg that is live at the broker but
        # not yet activated (waiting on the entry to fill) — it is still resting
        # protection and must count as open.
        return self.status in ("new", "partially_filled", "accepted", "pending_new", "held")

    def is_filled(self) -> bool:
        return self.status == "filled"
