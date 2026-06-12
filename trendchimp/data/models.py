from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float = 0.0
    trade_count: int = 0


@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    ask_price: float
    ask_size: float
    bid_price: float
    bid_size: float


@dataclass
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    size: float


@dataclass
class InternalPosition:
    """A position snapshot. qty is SIGNED: positive for long, negative for short."""

    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    unrealized_plpc: Decimal
    side: str = "long"  # "long" or "short" — mirrors sign(qty)

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass
class InternalAccount:
    buying_power: Decimal
    cash: Decimal
    portfolio_value: Decimal
    equity: Decimal
    currency: str = "USD"
