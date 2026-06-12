from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeIntent(str, Enum):
    """Disambiguates what a BUY/SELL is actually doing in a long+short book."""

    ENTER_LONG = "enter_long"    # BUY  — open long on N-day high breakout
    EXIT_LONG = "exit_long"      # SELL — close long on M-day low breakout
    ENTER_SHORT = "enter_short"  # SELL — open short on N-day low breakout
    EXIT_SHORT = "exit_short"    # BUY  — cover short on M-day high breakout

    @property
    def is_entry(self) -> bool:
        return self in (TradeIntent.ENTER_LONG, TradeIntent.ENTER_SHORT)

    @property
    def is_exit(self) -> bool:
        return self in (TradeIntent.EXIT_LONG, TradeIntent.EXIT_SHORT)


@dataclass
class Signal:
    symbol: str
    side: OrderSide
    strategy_name: str
    timestamp: datetime
    intent: TradeIntent
    strength: float = 1.0  # 0.0–1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata for entries carries: entry_price, atr (N), channel_high, channel_low,
    #   exit_channel_high, exit_channel_low, stop_price (2N), entry_channel, exit_channel
