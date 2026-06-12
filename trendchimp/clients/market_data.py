from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from trendchimp.data.models import Bar

logger = logging.getLogger(__name__)

# Map our config timeframe strings to alpaca-py TimeFrame objects.
_TIMEFRAME_MAP = {
    "1Min": ("Minute", 1),
    "5Min": ("Minute", 5),
    "15Min": ("Minute", 15),
    "1Hour": ("Hour", 1),
    "1Day": ("Day", 1),
}


class MarketDataClient:
    """Historical stock bar access, used to warm up strategy indicators."""

    def __init__(self, stock_client: Any, feed: str = "iex") -> None:
        self._stock = stock_client
        self._feed = feed

    @staticmethod
    def _resolve_timeframe(timeframe: str) -> Any:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        unit_name, amount = _TIMEFRAME_MAP.get(timeframe, ("Day", 1))
        return TimeFrame(amount, getattr(TimeFrameUnit, unit_name))

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Fetch historical bars for one symbol, oldest first."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=self._resolve_timeframe(timeframe),
            start=start,
            end=end,
            feed=DataFeed(self._feed),
        )
        response = self._stock.get_stock_bars(request)
        raw = response.data.get(symbol, [])
        bars = [
            Bar(
                symbol=symbol,
                timestamp=b.timestamp,
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=float(b.volume),
                vwap=float(getattr(b, "vwap", 0.0) or 0.0),
                trade_count=int(getattr(b, "trade_count", 0) or 0),
            )
            for b in raw
        ]
        logger.debug("Fetched %d %s bars for %s", len(bars), timeframe, symbol)
        return bars
