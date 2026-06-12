from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from trendchimp.data.models import Bar
from trendchimp.signals.models import Signal


class BaseStrategy(ABC):
    """Abstract base for all strategies.

    Strategies are isolated from broker code — they only receive market data and
    return Signal objects. The same on_bar() is used identically during live
    trading, warmup replay, and (future) backtesting.
    """

    name: str = ""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.parameters = params or {}

    @abstractmethod
    async def on_bar(self, bar: Bar) -> list[Signal]:
        """Called for each new bar. Return signals or an empty list."""

    def get_required_history(self) -> int:
        """Minimum number of bars needed before this strategy can emit signals."""
        return 0

    def seed_position(self, symbol: str, qty: float) -> None:
        """Prime the strategy's directional state from an existing position.

        Called during warmup so that a restart with an open position resumes in
        the correct long/short state instead of re-entering. Default is a no-op.
        """
