from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from trendchimp.data.feed import MarketDataFeed
from trendchimp.data.models import Bar

if TYPE_CHECKING:
    from trendchimp.config.settings import TrendChimpSettings

logger = logging.getLogger(__name__)

TradeUpdateHandler = Callable[[Any], Awaitable[None]]


class StreamManager:
    """Owns WebSocket stream lifecycle and routes events to MarketDataFeed."""

    def __init__(
        self,
        feed: MarketDataFeed,
        settings: "TrendChimpSettings",
        stock_stream: Any | None = None,
        trading_stream: Any | None = None,
    ) -> None:
        self._feed = feed
        self._settings = settings
        self._stock_stream = stock_stream
        self._trading_stream = trading_stream
        self._trade_update_handlers: list[TradeUpdateHandler] = []
        self._tasks: list[asyncio.Task] = []

    def add_trade_update_handler(self, handler: TradeUpdateHandler) -> None:
        self._trade_update_handlers.append(handler)

    async def start(self) -> None:
        symbols = [s.upper() for s in self._settings.trading.symbols]

        if self._stock_stream:
            self._stock_stream.subscribe_bars(self._on_bar, *symbols)
            self._tasks.append(asyncio.create_task(self._stock_stream._run_forever()))
            logger.info("Stock data stream started for %s", symbols)

        if self._trading_stream:
            self._trading_stream.subscribe_trade_updates(self._on_trade_update)
            self._tasks.append(asyncio.create_task(self._trading_stream._run_forever()))
            logger.info("Trading stream started")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("All streams stopped")

    async def _on_bar(self, bar: Any) -> None:
        internal = Bar(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            vwap=float(getattr(bar, "vwap", 0.0) or 0.0),
            trade_count=int(getattr(bar, "trade_count", 0) or 0),
        )
        await self._feed.publish(internal)

    async def _on_trade_update(self, update: Any) -> None:
        for handler in self._trade_update_handlers:
            try:
                await handler(update)
            except Exception:
                logger.exception("Trade update handler error")
