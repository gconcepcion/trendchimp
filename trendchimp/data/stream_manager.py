from __future__ import annotations

import asyncio
import inspect
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
        symbols: list[str] | None = None,
    ) -> None:
        self._feed = feed
        self._settings = settings
        self._stock_stream = stock_stream
        self._trading_stream = trading_stream
        # Subscribe to the symbols actually being traded (the resolved universe),
        # NOT the raw configured list — otherwise the stream watches the wrong
        # tickers and no bars ever reach the strategy handlers.
        source = symbols if symbols is not None else settings.trading.symbols
        self._symbols = [s.upper() for s in source]
        self._trade_update_handlers: list[TradeUpdateHandler] = []
        self._tasks: list[asyncio.Task] = []

    def add_trade_update_handler(self, handler: TradeUpdateHandler) -> None:
        self._trade_update_handlers.append(handler)

    async def start(self) -> None:
        symbols = self._symbols

        if self._stock_stream:
            self._stock_stream.subscribe_bars(self._on_bar, *symbols)
            self._tasks.append(asyncio.create_task(self._stock_stream._run_forever()))
            logger.info("Stock data stream started for %s", symbols)

        if self._trading_stream:
            self._trading_stream.subscribe_trade_updates(self._on_trade_update)
            self._tasks.append(asyncio.create_task(self._trading_stream._run_forever()))
            logger.info("Trading stream started")

    async def stop(self) -> None:
        # Gracefully close each websocket. Alpaca's _consume loop watches the
        # stop queue and sends a close frame (via stream.close()) before the
        # run loop exits. Cancelling the task instead would kill the coroutine
        # mid-recv without ever closing the socket, so Alpaca keeps the old
        # connection alive server-side and rejects the next start with
        # "connection limit exceeded" (only one data connection is allowed).
        for stream in (self._stock_stream, self._trading_stream):
            if stream is None:
                continue
            # alpaca-py exposes async stop_ws(); tolerate a sync stop_ws/stop or a
            # missing one (test doubles) so a bad close path can't leave the socket
            # open server-side — the tasks are still cancelled as a backstop below.
            stop = getattr(stream, "stop_ws", None) or getattr(stream, "stop", None)
            if stop is None:
                logger.warning("Stream %s has no stop_ws()/stop() — relying on task cancel",
                               type(stream).__name__)
                continue
            try:
                result = stop()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Error signaling stream to stop")

        if self._tasks:
            # Give the run loops time to notice the stop signal and close
            # cleanly; only cancel as a last resort if they hang.
            _, pending = await asyncio.wait(self._tasks, timeout=10)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
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
