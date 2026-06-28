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
        on_reconnect: Callable[[], Any] | None = None,
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
        # Reconnect supervision: a dropped websocket is otherwise silent and never
        # restarted, so fills stop arriving and bars stop processing. on_reconnect is
        # fired after each successful reconnect so the runner can re-assert protection.
        self._on_reconnect = on_reconnect
        self._stopping = asyncio.Event()
        self._reconnect_base_delay = 1.0
        self._reconnect_max_delay = 60.0

    def add_trade_update_handler(self, handler: TradeUpdateHandler) -> None:
        self._trade_update_handlers.append(handler)

    async def start(self) -> None:
        symbols = self._symbols

        if self._stock_stream:
            async def _stock_factory():
                self._stock_stream.subscribe_bars(self._on_bar, *symbols)
                await self._stock_stream._run_forever()

            self._tasks.append(asyncio.create_task(
                self._run_with_reconnect("stock data", _stock_factory)))
            logger.info("Stock data stream started for %s", symbols)

        if self._trading_stream:
            async def _trading_factory():
                self._trading_stream.subscribe_trade_updates(self._on_trade_update)
                await self._trading_stream._run_forever()

            self._tasks.append(asyncio.create_task(
                self._run_with_reconnect("trading", _trading_factory)))
            logger.info("Trading stream started")

    def request_stop(self) -> None:
        """Signal the reconnect supervisors to stop retrying (graceful shutdown)."""
        self._stopping.set()

    async def _run_with_reconnect(
        self, label: str, factory: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a stream coroutine, reconnecting with exponential backoff if it drops.

        Exits cleanly once a stop is requested. After each reconnect the on_reconnect
        hook fires so the runner can re-assert protective stops on the live book (a
        gap in the stream can hide a fill whose stop never attached).
        """
        attempt = 0
        while not self._stopping.is_set():
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s stream errored", label)
            if self._stopping.is_set():
                break
            attempt += 1
            delay = min(self._reconnect_base_delay * (2 ** (attempt - 1)),
                        self._reconnect_max_delay)
            logger.warning("%s stream dropped — reconnecting in %.1fs (attempt %d)",
                           label, delay, attempt)
            if delay > 0:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                    break  # stop requested during the backoff
                except asyncio.TimeoutError:
                    pass
            if self._on_reconnect is not None:
                try:
                    result = self._on_reconnect()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("on_reconnect hook failed")
        logger.info("%s stream supervisor stopped", label)

    async def stop(self) -> None:
        # Tell the reconnect supervisors this is a deliberate shutdown so they don't
        # treat the closing socket as a drop and try to reconnect.
        self._stopping.set()
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
